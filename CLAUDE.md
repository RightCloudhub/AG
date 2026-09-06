# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AgenticGraphRAG — a graph-augmented multi-hop reasoning QA system: knowledge graph as structured memory, an agent loop (plan → execute → reflect → guardrails) as the decision brain. Python 3.12+, Pydantic v2, LangGraph, FastAPI. Primary docs (README, PRD, plan/, docs/) are written in Chinese; code and comments are English.

The complete documentation map — every doc with a one-line purpose, plus which doc is the authority for each kind of status — is [docs/README.md](docs/README.md).

## Commands

```bash
# Setup (uv; editable install with dev extras)
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env          # LLM_API_KEY optional — offline path needs none

# Lint / format (CI-enforced)
ruff check src tests scripts
ruff format --check src tests scripts

# Tests (CI gate: unit tests + coverage ≥80%). tests/ contains only tests/unit.
pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q
pytest tests/unit/test_scoring.py -q          # single file
pytest tests/unit -k "guardrails" -q          # by keyword

# Hard code-metrics gate (file ≤300 lines, function ≤50, nesting ≤3, ≤3 positional params, CC ≤10)
python scripts/check_code_metrics.py

# Offline dev loop — deterministic, no LLM, no Docker (run-cases loads seed triples itself)
agr-run-cases --no-llm                        # 20 cases → reports/poc_run.jsonl + accuracy
agr-query --no-llm "Who is the CEO of Apex Holdings?"
python -m agentic_graphrag score

# Knowledge pipeline (offline seed variant; full extraction needs LLM_API_KEY + Neo4j)
agr-ingest
agr-build-graph --triples data/processed/seed_triples.jsonl --no-llm   # falls back to memory graph if Neo4j down; --memory-graph forces
agr-index --no-embed

# API + trial Web UI
agr-api                                       # http://localhost:8000/web ; POST /v1/query ; POST /v1/query/stream (SSE); /healthz

# Enterprise ops (ENT-01…08; offline-friendly)
PYTHONPATH=src python -m agentic_graphrag.knowledge.ingest_worker --once   # consume queued upload tasks (API does NOT auto-start the worker)
PYTHONPATH=src python scripts/prune_data_files.py --dry-run                # retention pruning per configs/default.yaml retention:

# Infra (optional; only live paths need it)
docker compose up -d                          # Neo4j 7474/7687 (neo4j/agentic-graphrag), Qdrant 6333

# Gate / regression scripts
./scripts/verify_all.sh                       # master pack — see "Gates" below (--quick / --with-tests / --with-llm / --target=URL)
./scripts/g1_to_g2_gate.sh                    # C1/C2/C3 summary (--with-llm for live)
PYTHONPATH=src .venv/bin/python scripts/p3_ev_offline.py
```

All subcommands are also reachable as `python -m agentic_graphrag <command>` (ingest, build-graph, index, run-cases, run-baseline, score, eval, gen-cases, pilot-triples, badcase, spotcheck, score-spotcheck, export-reasoning-schema, query). Only some have `agr-*` console scripts — `score`, `spotcheck`, `score-spotcheck` and `export-reasoning-schema` are `python -m` only. `pyproject.toml` sets `pythonpath = ["src"]` for pytest, so tests run without install; scripts use `PYTHONPATH=src`.

## Architecture

### The offline/live duality (read this first)

Every layer has two implementations, and the offline one is the default everywhere. This is what keeps CI and the 20-case eval deterministic:

| Layer | Offline (default) | Live (opt-in) |
|---|---|---|
| Graph | `InMemoryGraphStore` + `data/processed/seed_triples.jsonl` | Neo4j |
| Vector | in-memory + persisted embeddings if present | Qdrant |
| LLM | `MockLLMProvider` + offline answer heuristics | real provider (OpenAI-compatible) |

BM25 fulltext and the doc store have **no** live backend — `create_live_bundle()` uses the same in-memory BM25 store as offline, and falls back to the memory vector store if Qdrant is unreachable (graph only falls back with `allow_memory_graph_fallback`).

**The two live switches are independent** (`api/service.py:268`): `AGR_ALLOW_LLM=1` swaps in the real LLM but leaves stores untouched; `AGR_USE_LIVE_STORES=1` swaps stores to Neo4j+Qdrant but leaves the LLM untouched. `agr-api` defaults to `QueryService.create_offline()` in the FastAPI lifespan; startup validation (`api/app.py:105`) rejects each flag if its prerequisites (Neo4j/Qdrant settings, a non-placeholder `LLM_API_KEY`) are missing. CLI equivalents are `--no-llm` / `--neo4j` / `--memory-graph`.

The offline answer path (`generation/offline_answer.py` + `generation/offline_heuristics/rules_*.py`) is a large rule set hardcoded to the demo corpus in `data/raw/` — it exists to make `--no-llm` evals deterministic, is excluded from coverage, and is **not** the production path. Don't "fix" live-LLM behavior by editing it, and vice versa.

### Composition root and protocols

`stores/factory.py` is the single composition root: `create_offline_bundle()` / `create_live_bundle()` return a `StoreBundle` (graph/vector/fulltext/docs). Application code depends only on the protocols in `stores/interfaces.py` (`GraphStore`, `VectorStore`, `FulltextStore`, `DocStore`) — never on Neo4j/Qdrant client types. Live backends are imported lazily inside the factory so offline paths don't need those deps at runtime. Neo4j is split `neo4j_store` / `neo4j_queries` / `neo4j_codec`; Qdrant lives inside `vector_store.py` (there is no `qdrant_store.py`).

### Query flow

`api/routes/query.py` → `QueryService` (`api/service.py`, holds the bundle + audit store + review queue + retrieval cache + multi-tenant budget) → `agent/loop.run_query()`:

0. **Chitchat short-circuit** (`agent/chitchat.py`) runs *before* triage in both `loop.py` and `loop_stream.py`: greetings/capability questions skip retrieval entirely, even under `force_agentic`.
1. **Triage** (`agent/triage.py`) routes to Fast Path or Agentic (`Route` has exactly these two members); Fast Path (`agent/fast_path.py`) can escalate back to Agentic on weak evidence (`should_escalate_fast_path`).
2. **Agentic** = LangGraph `StateGraph`: `planner → executor → critic → (loop back to executor | answer)`, compiled with a checkpointer (`agent/checkpointer.py`) for durable state. Node handlers live in `agent/loop_runtime.py`/`loop_handlers.py`; `loop.py` only wires the graph. Planner emits a DAG (`agent/plan_dag.py`) that can gain sub-questions mid-run.
3. **Executor** (`agent/executor.py` + `executor_plan.py`/`executor_dispatch.py`, tools in `agent/tools/registry.py`) fans out to three retrievers — vector, graph beam search (`retrieval/graph.py` + `graph_beam`/`graph_paths`/`graph_relation`), BM25 (`retrieval/fulltext.py`) — fused via RRF (`retrieval/fusion.py`), through `RetrievalCache` (`retrieval/cache.py`, invalidated by index version).
4. **Guardrails** (`agent/guardrails.py`, config `guardrails:` section): max hops, max LLM calls, token budget (`llm/budget.py`, tenant-level in `budget_policy.py`), timeout, recursion limit. `llm/circuit.py` is a consecutive-failure circuit breaker on the live LLM HTTP client.

**All three retrievers return the same `Candidate` shape** (`retrieval/contracts.py`) — fusion, agent memory, citation binding, and eval evidence-recall all consume it. Adding a retriever means conforming to that contract, not inventing a new one.

The output contract is `ReasoningChain` (`generation/trace.py`); its JSON Schema is `configs/schema/reasoning_chain_v1.json` (regenerate with `export-reasoning-schema`). Chains are persisted to the audit store; `POST /v1/feedback` links user feedback to a chain and enqueues inaccurate ones into the review queue.

### Knowledge pipeline (`knowledge/`)

`ingest.py` (docs → chunks) → `extraction.py` (facade over `extract_core`/`extract_pipeline`/`extract_chunk`; per-chunk LLM extraction with a **resume journal, retry, and quarantine** for failures) → `schema_check.py` (validates against `configs/schema/domain_v0.yaml` + confidence threshold; **non-conforming triples are rejected and recorded, never entering the graph**) → `resolution.py`/`resolution_merge.py` (three-tier entity disambiguation) → `graph_builder.py` (triples → graph records → `GraphStore`).

`incremental.py` handles new docs against an existing graph: extract → conflict detect → high-confidence auto-merge, low-confidence into the review queue (`knowledge/review/queue.py`). `pilot_triples.py` supplies deterministic triples for the synthetic pilot corpus so graph build and gold-case generation need no live LLM — it mirrors `scripts/generate_pilot_corpus.py` and both must be changed together. `ingest_tasks.py` is the durable upload task state machine consumed by `ingest_worker.py`.

### API surface

All routers are `prefix="/v1"`. `AuthRateLimitMiddleware` (`api/auth.py`) wraps everything; role checks are per-route via `Depends(require_role(...))` from `api/rbac.py`; responses go through the shared success/error envelope (`api/envelope.py`, `api/errors.py`).

- **query.py**: `POST /v1/query`, `POST /v1/query/stream` (SSE)
- **knowledge.py**: `POST /v1/docs` (admin/operator), `GET /v1/ingest-tasks/{id}`, `GET /v1/review-queue`, `POST /v1/review-queue/{id}/decision` (admin/operator), `GET /v1/audit/queries/{id}`, `POST /v1/feedback`, `GET /v1/metrics` (admin), `GET /v1/graph/entities`
- **admin.py** (all admin-only): `GET /v1/traces/{id}`, `GET /v1/budget/snapshot`, `GET /v1/audit-events`
- **console.py** (P5-UI-02 M0): `GET /v1/me` (identity echo `{tenant_id, user_id, role}`; anonymous ⇒ reader, so the UI derives nav visibility from role without 403 probing), `GET /v1/ingest-tasks` (tenant-scoped paginated list, operator+)
- **app.py**: `GET /healthz`, `GET /metrics-prom` (Prometheus text), `GET /web` (trial UI)

`QueryService` is deliberately split across `service.py` / `service_helpers.py` / `service_query.py` / `service_stream.py` / `service_agent.py` / `service_telemetry.py` for the file-size gate.

### Config

`config.py` merges `configs/default.yaml` (`AppConfig`, tunables; `tenants:` per-tenant limits and `retention:` periods live in `config_enterprise.py` models) with `.env` (`Settings`, secrets/endpoints via pydantic-settings); env overrides YAML. Repo root is auto-discovered (`AGENTIC_GRAPHRAG_ROOT` to override), and all data paths resolve against it via `resolve_path()` regardless of cwd. LLM prompts are markdown files in `configs/prompts/` loaded by `load_prompt(name)`.

`AGR_*` switches are read from `os.environ` directly (not `.env`) and are the **only** sanctioned direct-environ reads — new settings belong in `AppConfig` or `Settings`: `AGR_ALLOW_LLM`, `AGR_USE_LIVE_STORES`, `AGR_REQUIRE_AUTH`, `AGR_API_KEYS=tenant:key:role,...` (roles admin/operator/reader; bare `tenant:key` parses as reader), `AGR_RATE_LIMIT_QPS`, `AGR_RATE_LIMIT_CONCURRENT`, `AGR_TRUST_X_USER_ID` (default off — `X-User-Id` is not budget identity), `AGR_LOG_LEVEL/FILE`, `AGR_REDACTION_ENABLED/PATTERNS`, `AGR_OTEL_ENABLED/ENDPOINT/SAMPLE_RATE`, `AGR_API_HOST/PORT/RELOAD`.

### Enterprise layer (ENT-01…08, 2026-07-25)

Observability lives in `observability/`: `logging_setup.py` (JSON logs + request/query/tenant/user contextvars), `audit_events.py` (append-only security event JSONL), `metrics.py` (query-level metric collection; Prometheus text is rendered by `prometheus_metrics_text()` in `api/routes/admin.py`), `redaction.py` (PII scrub, default off), `otel_bridge.py` (optional OTel via `.[otel]` extra; coverage-omitted). `tenant_id` threads from `Principal` through all store protocols, the three retrievers, and the agent loop (retrieval-cache keys are tenant-scoped, so tenant runs use the cache instead of bypassing it); uploads are governed (5MB/20 files/md|txt — PDF is rejected with an explicit "not supported yet" until a text extractor exists) and land in a durable `IngestTaskStore` consumed by a separately-run `ingest_worker`. ENT-07 (RPA/webhooks) is explicitly out of scope. Status authority: `docs/ENTERPRISE_READINESS.md` §3.5; ops procedures: `docs/ops-runbook.md`.

### Evaluation

Datasets live in `evals/datasets/*.jsonl` (poc, dev/heldout/guardrail splits via `eval/split_sets.py`, g2_* generated gold sets; spec in `ANNOTATION_SPEC.md`). Gold cases are generated deterministically from templates (`eval/gold_gen.py` + `eval/gold_templates/`) by walking the graph — no LLM. `run-cases` writes `reports/*.jsonl` + accuracy JSON; `run-baseline` (`eval/baseline_rag.py`) runs the pure-vector RAG baseline on the same cases with the same scoring helpers for a fair delta; `badcase` does attribution. Metrics (`eval/metrics.py`, `metrics_evidence.py`): accuracy, evidence recall, latency, cost, fabrication rate.

### Gates

`./scripts/verify_all.sh` is the master verification pack. It writes `reports/verify_all/VERIFY_ALL_status.{json,md}` and reports two **separate** verdicts: `engineering_pass` (offline suite: code metrics, ruff, tests, pilot corpus, P95 smoke, guardrails) and `formal_pass` (product/live AC gates — expected `false` until a real authorized domain, live held-out evidence, human gold sign-off and staging P95 exist). Offline results are never allowed to set `formal_pass`. Use `--with-tests`, `--with-llm`, `--target=http://127.0.0.1:8000` (the only way to close AC-4), `--require-formal` to fail on open product gates.

## Conventions

- **Binding rules are consolidated in `plan/engineering/rules.md`** with their enforcement mechanism (CI / gate script / review). Design-vs-implementation divergences are marked "⚠ 差异" in `plan/workstreams/` docs and ledgered in `docs/IMPORTANT.md`. The business-logic integrity audit (BL-01…14, file:line evidence + fix status in §7) lives in `docs/BUSINESS_LOGIC.md`.
- **Architecture boundaries (review-enforced, `rules.md` §6).** LangGraph is confined to `agent/` at a pinned version; do **not** introduce LangChain retrieval/chain abstractions — retrieval and LLM calls go through this repo's `retrieval/` and `llm/` interfaces (ADR-005). Graph nodes are "state in → state out" so unit tests never need the LangGraph runtime. New features must run under `--no-llm` + in-memory backends or explicitly declare themselves live-only; never pull an online dependency into the CI path.
- **`docs/IMPORTANT.md` is the debt/deferral ledger.** Every intentionally deferred, blocked, or not-doing item lives there. When you close or defer a task, update it *in the same change set* (plus the phase checklist in `plan/phases/` and any gate JSON / risk status). Task status markers are uniform: `[ ]` not started, `[~]` in progress, `[x]` done, `[-]` cancelled (state the reason). Task IDs like `P2-KG-01`, `P3-PERF-06`, `C1/C2/C3` refer to `plan/` phase files.
- **The 2026-08-08 BL-fix changeset landed without running the gates** (the authoring environment had no Python/venv). Run the checklist in `docs/BL_FIX_VERIFICATION.md` before trusting those fixes, and archive that checklist once the gates pass.
- **Changing a technology choice requires a new ADR in `plan/engineering/tech-stack.md` first**, then code.
- **Engineering-done ≠ product-accepted.** Gates G1–G4 (`plan/roadmap.md`) require live-LLM/held-out evidence; offline synthetic results must not be presented as gate evidence. README's status table reflects this split — keep it honest when updating.
- **Hard code metrics are enforced by `scripts/check_code_metrics.py`** (same limits as the table above). This is why modules are deliberately split (`loop` / `loop_handlers` / `loop_runtime`, `executor` / `executor_plan` / `executor_dispatch`, `service` / `service_helpers` / `service_query`). New modules target ~200–400 lines; put constants in module-level named constants, not inline literals.
- **Coverage omit list in `pyproject.toml` is intentional** (CLI glue, `stores/neo4j_store.py`, live LLM client, `generation/answer.py`, offline heuristic `rules_*.py`, `otel_bridge.py`). If you add code to an omitted module, either cover it or keep the omission justified; don't silently expand the list.
- **Trial UI (`web/`)**: zero-build — no npm, no bundler; Vue 3 ESM runtime only, vendored-first and pinned (ADR-006, mirrored in `docs/EXTERNAL_RUNTIMES.md`). Dynamic text goes through mustache or `textContent`; `v-html` and `.innerHTML` are forbidden. JS/CSS/HTML obey the same size and complexity limits as Python (review-enforced — the metrics script only scans Python). SSE consumers must handle every event type (`cache_hit/triage/thinking/sub_question/hop_done/answer/error`) and silently ignore unknown ones; SSE is true incremental via LangGraph `stream(updates)` (`agent/loop_stream.py` + `api/service_stream.py`). DOM/module changes must update the structure and injection-safety assertions in `tests/unit/test_web_claude_ui.py` and `tests/unit/test_web_console.py` (console shell / views / widgets, P5-UI-02).
- Ruff: line length 100, target py312, rules `E,F,I,UP,B`. Tests: pytest with `asyncio_mode = "auto"`.
- Some environments here have no Docker: Neo4j runs from a tarball under `/tmp` with a Temurin 17 `JAVA_HOME` (see `docs/EXTERNAL_RUNTIMES.md`), and the LLM gateway is prone to 403s — prefer offline paths unless the task requires live backends.
