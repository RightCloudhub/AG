# Clean-Room Design vs. Actual Implementation

Diff of [`ARCHITECTURE_DESIGN.md`](ARCHITECTURE_DESIGN.md) (written blind, from the project name
only) against the code on `feat/all-phases-complete` as of 2026-07-26.

**Headline: the design and the implementation agree on essentially the whole skeleton.** Of 26
designed capabilities, 20 are present and structurally equivalent, 2 are present as interface seams
without an implementation, and 4 are absent. Three places where the code deliberately does something
the design did not anticipate are, on inspection, better than the design.

These findings are **not** ledgered in `docs/IMPORTANT.md`. They are an external design's opinion,
not project-sanctioned debt. Promote individual items there only if you agree they are debt.

---

## 1. Convergent (design ≡ code)

Independently arriving at the same structure is weak evidence that the structure is forced by the
problem. These matched without qualification:

| Design element | Implementation | Note |
|---|---|---|
| Offline/live duality, offline default | `stores/factory.py`, `AGR_ALLOW_LLM` / `AGR_USE_LIVE_STORES` | Code is **stronger**: the two switches are independent, so you can test a live LLM against offline stores. The design treated "live" as one axis. |
| 4 store protocols + one composition root | `stores/interfaces.py`, `stores/factory.py` | Exact match |
| One `Candidate` shape across 3 retrievers | `retrieval/contracts.py` | Exact match |
| Versioned reasoning-chain export | `generation/trace.py` + `configs/schema/reasoning_chain_v1.json` | Exact match |
| Triage → {fast path, agentic} + escalation | `agent/triage.py:61` (`Route` has exactly 2 members), `agent/loop_policy.py` | Exact match |
| Chitchat short-circuit before triage | `agent/chitchat.py` | Exact match |
| Sub-question DAG, placeholders, topo-sort | `agent/plan_dag.py` — Kahn sort, `{from:sqN}` auto-wires `depends_on`, cycle→input order | Structurally exact |
| Critic with replan/answer verdict | `agent/critic.py` (`CriticAction`, `CriticScope`) | Exact match |
| Guardrails: hops/calls/tokens/timeout/recursion | `agent/guardrails.py`, `configs/default.yaml` | Exact match, plus extras — see §4 |
| Consecutive-failure circuit breaker | `llm/circuit.py:21` (threshold 2, cooldown, half-open) | Exact match |
| Hub-node degree defence | `retrieval/graph_beam.py:49,113` — `high_degree_threshold=30`, `looks_high_degree()` gates a hard relation-type filter | Exact match |
| Three-tier entity resolution | `knowledge/resolution.py` — docstring literally names the same three tiers | Exact match |
| Reject-never-repair schema gate | `knowledge/graph_builder.py:86` "never writes rejected", `knowledge/schema_check.py` | Exact match |
| Provenance + confidence per edge | `knowledge/graph_builder.py:39` — `{doc_id, chunk_id, span, confidence}` on relations *and* entities | Exact match |
| Extraction resume journal / retry / quarantine | `knowledge/extract_pipeline.py`, `extract_types.py` | Exact match |
| Abstention as a first-class outcome | `QueryStatus.NO_ANSWER` + `honest_fallback()`, wired at 5 sites (guardrail trip, recursion recovery, no evidence, model no-answer, citation failure) | Exact match |
| Citation binding, regenerate-once, then abstain | `generation/citations.py`, `generation/answer.py:221` | Match — but see **D2** |
| RRF fusion | `retrieval/fusion.py` | Exact match |
| Cache: index-version invalidation, tenant-scoped answers | `retrieval/cache.py:133,147` — retrieval key = `tenant|norm(query)|tools+version`; answer key = `tenant|user|params|version` | Exact match |
| SSE incremental streaming | `agent/loop_stream_events.py` — `triage/thinking/sub_question/hop_done` | Exact match |
| RBAC, tenancy, budgets, audit events | `api/rbac.py`, `llm/budget_policy.py`, `observability/audit_events.py` | Exact match |
| Evidence recall + fabrication rate | `eval/metrics_evidence.py:93,145` | Match — but see **D2** |
| Vector-RAG baseline on identical cases | `eval/baseline_rag.py` | Exact match |
| Deterministic gold gen by graph walk | `eval/gold_gen.py` + `eval/gold_templates/` | Exact match |
| Critic non-convergence defence | `agent/loop_handlers.py` — `memory.is_duplicate_subquestion()`, `memory.is_excluded()` | The design named this as a *risk mitigation*; code has it |

---

## 2. Divergences — design specifies, code lacks

Ordered by how much they matter.

### D1 · The DAG is built but never executed as a DAG ⚠ highest impact

`plan_dag.py:124 ready_subquestions()` — which returns *all* pending nodes whose dependencies are
satisfied, i.e. the parallel-branch API — is defined, re-exported from `planner.py:16,32`, and
unit-tested (`tests/unit/test_planner_dag.py:44`), **but has no runtime caller.** The agent loop
instead walks `current_index` linearly (`loop_handlers.py:70,81,151,213`), one sub-question per
executor node visit.

Confirming evidence: the codebase is synchronous — 11 `async def` total, all in the API layer. There
is no `asyncio.gather` / `TaskGroup` / `to_thread` anywhere in `agent/` or `retrieval/`.

**Consequence.** The DAG buys dependency-correct ordering and placeholder binding — both real — but
not latency. For the doc's own worked example, `q2` (CEO) and `q3` (HQ) are independent and both
depend only on `q1`; they run back-to-back instead of together. On an *n*-branch question, latency is
Σ(branches) where it could be max(branches). Given `query_timeout_seconds: 45` and an unmet 8s AC-4
target, this is the largest unexploited latency win in the system, and most of the work — the graph
model, readiness predicate, and its tests — is already done.

**Worse than latency.** `loop_runtime.py:110` calls `guards.on_hop_start()` (`guardrails.py:128`,
`hop += 1`) on *every* executor visit, and each visit handles exactly one sub-question. So **the hop
counter is a count of executed sub-questions, not of reasoning depth** — breadth is billed to the
depth budget. With `max_hops: 4`, a plan of 6 sub-questions silently drops the last two
(`loop_handlers.py:64` sets `done` at `hop >= max_hops`), and the guardrail reports
`max_hops exceeded (5/4)`, which reads as "too deep" when the cause was "too wide". Nothing caps
`result.sub_questions` at `planner.py:58-60`, and `loop_handlers.py:249` can insert more mid-run.

### D2 · The citation gate stops at lexical overlap; *entailment* is not checked

The runtime gate (`citations.py:100-108`) is three tiers, not one: every claim must (1) carry
`evidence_ids`, (2) cite an id present in the retrieved set, and (3) share **≥1 content token** with
the cited evidence (`claims_lexically_supported`, `min_overlap=1`). The function is self-aware —
*"not a full NLI check — still better than ID-only fabrication=0"*.

Tier 3 is satisfiable by the **subject entity alone**, independent of the relation asserted. Evidence
`Apex Holdings -[FOUNDED_BY]-> X` and claim *"Apex Holdings' CEO is Y"* share `{apex, holdings}` and
pass. The gate cannot tell which relation the evidence states.

Evaluation is weaker still: `metrics_evidence.py:145 fabrication_rate()` uses `_claims_unbound()`
(:175), which checks **only that `evidence_ids` is non-empty** — not tier 2, not tier 3. Its docstring
says "AC-7 proxy"; its name reads as "fabrication rate".

**Failure mode.** A claim citing a real, retrieved, but relationally-wrong evidence id passes the
gate, is never regenerated, never triggers `honest_fallback`, and scores as non-fabricated. Note this
is the mirror of the project's own BL-02 (pure-Chinese claims fail tier 3 unconditionally, because
`_content_tokens` splits on whitespace): tier 3 is simultaneously too strict for CJK and too loose
for Latin text. **Fixing BL-02 with CJK segmentation makes this worse** — unigrams make the ≥1
overlap easier to satisfy. The two must be designed together.

### D3 · No temporal validity on edges

Zero occurrences of `valid_from` / `valid_to` / `valid_at` / `as_of` / `temporal` anywhere in `src`.
The graph carries `confidence` and provenance but no time scope.

**Consequence.** Time-scoped relations — `ceo`, `headquartered_in`, ownership, pricing — are stored
as timeless. Re-ingesting a corpus that contains both the 2019 and 2025 CEO produces two `ceo` edges
with no way to prefer the current one; `graph_builder.py:53` resolves the collision by **higher
confidence**, which is uncorrelated with recency. The design's staleness mitigation (validity fields
+ recency term in path scoring) has no counterpart in code. Low urgency on a static demo corpus,
blocking for any real deployment with an update cadence.

### D4 · Reranker is a seam, not an implementation

`fusion.py:28,34` defines a `Reranker` Protocol and an `IdentityReranker` that returns its input
unchanged; `executor.py:76` threads an optional `reranker` through. No cross-encoder exists.

This is **correct engineering** — the interface is defined at the right place and nothing premature
was built behind it — but the design's F-11 should read "rerank seam present, unimplemented" rather
than "optional rerank."

### D5 · The correction loop does not close

`review/queue.py:150 decide()` sets `status`, `reviewer`, `decision_note`, `decided_at` and persists.
Nothing reads approved items back into a `GraphStore`. The API route
(`api/routes/knowledge.py:133`) records a `review_decision` audit event and returns.

**Consequence.** In the design's Figure 2, `feedback → review queue → curator → graph` is a closed
cycle; in code the last arrow is missing. A curator can approve a correction, and the graph is
unchanged. Feedback is currently an *audit* mechanism, not a *repair* mechanism. This is the one
divergence where the design's diagram is actively misleading about the code, and it is a small piece
of work relative to what it unlocks (the KG becoming an asset that improves).

**Already known.** `docs/BUSINESS_LOGIC.md` records this independently as **BL-03**, with one detail
this diff missed: `IncrementalUpdater._append_review` (`incremental.py:269`) writes pending conflicts
to a *separate* JSONL that `ReviewQueue` never reads, so `GET /v1/review-queue` cannot surface them
at all. Treat BL-03 as the authority for this finding.

### D6 · Chunking is fixed-size, not structure-aware

`knowledge/ingest.py:49 chunk_text(chunk_size=1200, overlap=150)` — a sliding window that prefers a
break at `\n\n`, `。`, or `. ` past ⅓ of the window. No heading, list, or table handling; nothing
propagates document structure into chunk metadata. Tables in particular will be split mid-row.

### D7 · Anchor linking is closed-vocabulary, with no embedding fallback

`agent/entity_mentions.py` resolves mentions via quoted spans, a lexicon built from the seed triples
(`default_lexicon_from_seed`), title-case spans, CJK spans, and `_fuzzy_lexicon_hit` (:188). There is
no embedding fallback.

**Consequence.** An entity absent from the lexicon cannot anchor, so graph traversal never starts and
the query silently degrades to BM25/vector. This is a defensible design — anchoring only to entities
that exist in the graph avoids a class of false starts — but it makes anchor recall a hard function
of lexicon coverage, and there is no metric isolating anchor-miss from retrieval-miss.

### D8 · Path verbalization is symbolic, not natural language

`retrieval/graph_paths.py:58–68` renders paths as `A -[RELATION#id]-> B` arrow notation. The design
called for natural-language verbalization (`"Acme Robotics is a subsidiary of Zenith Group"`) so the
generator sees one uniform evidence format. Minor, and arguably better for a machine reader — but it
means graph evidence and text evidence reach the LLM in visibly different formats, which is a known
source of uneven attention across evidence types.

### D9 · Only one ablation exists

`scripts/p3_ev_offline.py:91 _run_triage_ablation()` → `triage_ablation.json` (P3-EV-02, marked `[~]`
in `plan/phases/phase-3-optimization.md:44`). There is no `−graph`, `−fusion`, or `−critic` ablation.

**Consequence.** The vector-RAG baseline (`eval/baseline_rag.py`) establishes that *the system* beats
*plain RAG*. It cannot attribute that delta to the graph specifically, nor tell you whether the
critic loop or RRF fusion earns its cost. For a system whose entire thesis is "the graph is what
makes multi-hop work," the component-level evidence for that thesis is currently missing.

### D10 · Human gold sign-off is pending

`evals/datasets/GOLD_SIGNOFF.md` — every checklist box unchecked, `**Signed:** _(pending)_`. The
design flagged graph-walk gold cases as self-confirming and required a human-authored supplement;
that supplement is scaffolded but unsigned. Consistent with, and a direct cause of, `formal_pass:
false`.

---

## 3. Where the code is right and the design was wrong

### E1 · Retrieval is conditional, not always-three-way

The design asserted all three retrievers always run and fuse, justified by uncorrelated failure
modes. The code disagrees deliberately:

- `executor_plan.py::build_heuristic` sets `skip_vector=has_graph` — **vector search is dropped when
  graph specs are already present**, with the comment *"Skip remote embedding when graph already
  expanded (AC-4 latency)."*
- `choose_tools()` skips the LLM tool-planning call entirely when heuristics already produced a
  `graph_neighbors` / `graph_path` / `graph_subgraph` handle: *"Remote LLM tool-planning is expensive
  (seconds–tens of seconds). Use it only when heuristics have no graph/path handle."*

Both are correct calls under a live P95 target; remote embedding and an extra LLM round-trip are the
two dominant latency costs, and the design ignored that in favour of a tidy principle.

Worth naming honestly, though: this **partially spends the redundancy argument**. On exactly the
queries where the graph produces a confident handle, the semantic retriever that would have caught a
wrong or incomplete graph is not consulted. The trade is latency-for-recall on graph-anchored
queries, which is probably right — but it means "three uncorrelated retrievers" describes the
capability, not the hot path, and D9's missing `−graph` ablation is what would tell you the true cost.

### E2 · Guardrails are coupled, not independent

`guardrails.py:20 min_recursion_limit(max_hops) = 2*max_hops + 5`, derived from the worst-case node
visit count, and applied via `max(configured, floor)` in both `from_app_config` and `with_overrides`
— *"Never ship a recursion_limit that the hop budget can exhaust."* Plus a `hard_max_hops=20` ceiling
that per-request overrides cannot exceed.

The design listed hop cap and recursion limit as two independent guardrails. Treating them as
independent is a latent bug — a caller raising `max_hops` would hit an unrelated LangGraph recursion
error instead of a clean hop-budget stop. The code prevents that structurally.

### E3 · Domain guards patch a known critic weakness

`agent/critic_guard.py::force_incomplete_multihop` forces another hop when a person-role question
("who is the CEO of…") has evidence that names organisations but no person. This is a pragmatic patch
for the critic under-calling `continue` on partially-answered multi-hop questions — a failure the
design named as a risk but offered no mechanism against.

---

## 4. Unanticipated in the design

**The offline heuristic answer layer.** `generation/offline_heuristics/rules_*.py` — 8 rule modules
(~1,400 lines) hardcoded to the demo corpus, coverage-omitted, driving `--no-llm` determinism. The
design assumed a mock LLM would suffice for offline determinism; the reality is a rule engine large
enough to be its own maintenance surface. It is honestly labelled in `CLAUDE.md` as not the
production path, and quarantined behind the omit list. Still: it is the single largest divergence in
*volume* between designed and actual system, and the design has no slot for it.

---

## 5. Summary scorecard

| # | Capability | Status |
|---|---|---|
| F-01 | Structure-aware chunking | ◐ fixed-size + boundary preference (**D6**) |
| F-02 | Extraction w/ journal, retry, quarantine | ● |
| F-03 | Ontology gate, reject-and-record | ● |
| F-04 | Three-tier entity resolution | ● |
| F-05 | Typed edges + confidence + provenance | ◐ no temporal validity (**D3**) |
| F-06 | Multi-index build | ● |
| F-07 | Incremental update + review queue | ● |
| F-08 | Swappable stores, offline default | ● |
| F-09 | Three retrievers | ◐ conditional, not always-on (**E1**) |
| F-10 | Beam search, path scoring, verbalization | ◐ symbolic rendering (**D8**) |
| F-11 | RRF fusion + rerank + cache | ◐ rerank is a no-op seam (**D4**) |
| F-12 | Triage + escalation | ● |
| F-13 | Sub-question DAG | ◐ built, topo-sorted, executed linearly (**D1**) |
| F-14 | Executor + evidence pool | ● |
| F-15 | Critic replan-or-stop | ● |
| F-16 | Guardrails | ● (**E2** improves on design) |
| F-17 | Grounded synthesis + citation binding | ● |
| F-18 | Faithfulness + abstention | ◐ abstention yes, entailment no (**D2**) |
| F-19 | Reasoning-chain export | ● |
| F-20 | Sync + SSE API | ● |
| F-21 | RBAC, tenancy, budgets | ● |
| F-22 | Logs, metrics, traces, audit | ● |
| F-23 | Feedback → curator → graph | ○ loop does not close (**D5**) |
| F-24 | Gold generation | ◐ graph-walk yes, human sign-off pending (**D10**) |
| F-25 | Accuracy/recall/fabrication/latency/cost | ◐ fabrication measures binding (**D2**) |
| F-26 | Baseline + ablations | ◐ baseline yes, 1 of 4 ablations (**D9**) |

● present · ◐ partial · ○ absent

**If you act on three things:** close the review→graph write-back (**D5**, small, unlocks the
knowledge flywheel), execute ready DAG branches concurrently (**D1**, the parallel API and its tests
already exist), and either add an entailment check or rename `fabrication_rate` to match what it
measures (**D2**).
