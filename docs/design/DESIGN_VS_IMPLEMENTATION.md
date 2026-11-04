# Clean-Room Design vs. Current Implementation

Current comparison with [ARCHITECTURE_DESIGN.md](ARCHITECTURE_DESIGN.md).
The original review, historical scorecard and subsequent review notes are in
[CHANGELOG.md](../../CHANGELOG.md#design-original-comparison).

The architecture shares the design's main structure. The table below records implemented
capabilities and remaining differences; live effectiveness and human sign-off remain separate
acceptance requirements. Design observations become project debt only when accepted into
[IMPORTANT.md](../reviews/IMPORTANT.md).

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

## 2. Current status of the identified differences

| ID | Capability | Current implementation / boundary |
|---|---|---|
| D1 | Parallel DAG branches | Ready independent branches retrieve concurrently through `agent/parallel_branches.py` and `agent/executor_dispatch.py`; critic evaluation remains ordered. |
| D2 | Entailment and citation support | `generation/entailment.py` performs a batched LIGHT check on the live path; CJK-aware lexical support and relation consistency also apply. Offline and mock paths skip the model call. |
| D3 | Temporal validity | `valid_from` / `valid_to` support dated succession and historical facts through `knowledge/temporal.py`. Both conflicting facts need dates for temporal arbitration. |
| D4 | Reranking | Lexical and HTTP cross-encoder implementations are available. Default is `none`; real-model accuracy and P95 acceptance remain open. See [rerank evaluation](../../reports/rerank_eval.md). |
| D5 | Review write-back | `knowledge/review/executor.py` applies approved decisions to the graph through the review API. |
| D6 | Structure-aware chunking | API/CLI share document parsing and heading-aware Markdown token chunks, including breadcrumbs, tables and code boundaries. MinerU requires explicit configuration. See [BUSINESS_LOGIC.md](BUSINESS_LOGIC.md) §3.1.2. |

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

### D9 · Component-level evidence remains incomplete

`scripts/p3_ev_offline.py:91 _run_triage_ablation()` → `triage_ablation.json` (P3-EV-02, marked `[~]`
in `plan/phases/phase-3-optimization.md:44`). The full `−graph`, `−fusion`, and `−critic` attribution suite remains an acceptance item; requirement-register alignment and search-drift tooling are documented separately in [REQUIREMENT_REGISTER.md](REQUIREMENT_REGISTER.md).

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

`guardrails.py min_recursion_limit(max_hops) = 2*max_hops + 10`, derived from the worst-case node
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

**The offline heuristic answer layer.** `generation/offline_heuristics/rules_*.py` — demo-specific rule modules hardcoded to the demo corpus, coverage-omitted, driving `--no-llm` determinism. The
design assumed a mock LLM would suffice for offline determinism; the reality is a rule engine large
enough to be its own maintenance surface. It is honestly labelled in `CLAUDE.md` as not the
production path, and quarantined behind the omit list. Still: it is the single largest divergence in
*volume* between designed and actual system, and the design has no slot for it.

---

## 5. Summary scorecard

| # | Capability | Status |
|---|---|---|
| F-01 | Structure-aware chunking | ● heading-aware token chunks (**D6**) |
| F-02 | Extraction w/ journal, retry, quarantine | ● |
| F-03 | Ontology gate, reject-and-record | ● |
| F-04 | Three-tier entity resolution | ● |
| F-05 | Typed edges + confidence + provenance | ● 时效字段已补（**D3** 已落地） |
| F-06 | Multi-index build | ● |
| F-07 | Incremental update + review queue | ● |
| F-08 | Swappable stores, offline default | ● |
| F-09 | Three retrievers | ◐ conditional, not always-on (**E1**) |
| F-10 | Beam search, path scoring, verbalization | ◐ symbolic rendering (**D8**) |
| F-11 | RRF fusion + rerank + cache | ● rerank 已实现（lexical / HTTP cross-encoder，默认关闭；**D4** 已落地） |
| F-12 | Triage + escalation | ● |
| F-13 | Sub-question DAG | ● 就绪分支并发执行（**D1** 已落地） |
| F-14 | Executor + evidence pool | ● |
| F-15 | Critic replan-or-stop | ● |
| F-16 | Guardrails | ● (**E2** improves on design) |
| F-17 | Grounded synthesis + citation binding | ● |
| F-18 | Faithfulness + abstention | ● 蕴含校验已接入（**D2** 已落地） |
| F-19 | Reasoning-chain export | ● |
| F-20 | Sync + SSE API | ● |
| F-21 | RBAC, tenancy, budgets | ● |
| F-22 | Logs, metrics, traces, audit | ● |
| F-23 | Feedback → curator → graph | ● 审核决策回写图谱（**D5** 已落地） |
| F-24 | Gold generation | ◐ graph-walk yes, human sign-off pending (**D10**) |
| F-25 | Accuracy/recall/fabrication/latency/cost | ● relation-consistency scoring; live entailment remains a separate check (**D2**) |
| F-26 | Baseline + ablations | ◐ baseline yes, 1 of 4 ablations (**D9**) |

● present · ◐ partial · ○ absent
