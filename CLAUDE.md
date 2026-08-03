# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AgenticGraphRAG — a graph-augmented multi-hop reasoning QA system: knowledge graph as structured memory, an agent loop (plan → execute → reflect → guardrails) as the decision brain. Python 3.12+, Pydantic v2, LangGraph, FastAPI. Primary docs (README, PRD, plan/, docs/) are written in Chinese; code and comments are English.

## Commands

```bash
# Setup (uv; editable install with dev extras)
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env          # LLM_API_KEY optional — offline path needs none

# Lint / format / tests (CI-enforced; coverage ≥80%)
ruff check src tests scripts
ruff format --check src tests scripts
pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q
pytest tests/unit/test_scoring.py -q          # single file

# Hard code-metrics gate (file ≤300 lines, function ≤50, nesting ≤3, ≤3 positional params, CC ≤10)
python scripts/check_code_metrics.py

# Offline dev loop — deterministic, no LLM, no Docker
agr-run-cases --no-llm                        # 20 cases → reports/poc_run.jsonl + accuracy
agr-query --no-llm "Who is the CEO of Apex Holdings?"

# Knowledge pipeline (offline seed variant)
agr-ingest
agr-build-graph --triples data/processed/seed_triples.jsonl --no-llm
agr-index --no-embed

# API + trial Web UI
agr-api                                       # http://localhost:8000/web ; /v1/query ; /v1/query/stream (SSE); /healthz

# Enterprise ops (ENT-01…08; offline-friendly)
PYTHONPATH=src python -m agentic_graphrag.knowledge.ingest_worker --once   # consume queued upload tasks (API does NOT auto-start the worker)

# Master gate pack
./scripts/verify_all.sh                       # --quick / --with-tests / --with-llm / --target=URL
```

All subcommands are reachable as `python -m agentic_graphrag <command>` (ingest, build-graph, index, run-cases, run-baseline, score, eval, gen-cases, pilot-triples, badcase, spotcheck, score-spotcheck, export-reasoning-schema, query); only some have `agr-*` console scripts. `pyproject.toml` sets `pythonpath = ["src"]` for pytest; scripts use `PYTHONPATH=src`.

## Architecture

### Offline/live duality (read this first)

Every layer has two implementations; offline is the default everywhere, keeping CI and evals deterministic:

| Layer | Offline (default) | Live (opt-in) |
|---|---|---|
| Graph | `InMemoryGraphStore` + `data/processed/seed_triples.jsonl` | Neo4j |
| Vector | in-memory + persisted embeddings | Qdrant |
| LLM | `MockLLMProvider` + offline answer heuristics | real provider (OpenAI-compatible) |

BM25 fulltext and the doc store have **no** live backend — `create_live_bundle()` reuses the in-memory BM25 store and falls back to the memory vector store if Qdrant is unreachable.

The two live switches are **independent** (`api/service.py`): `AGR_ALLOW_LLM=1` swaps the LLM only; `AGR_USE_LIVE_STORES=1` swaps stores only. Startup validation rejects each flag if its prerequisites are missing. CLI equivalents: `--no-llm` / `--neo4j` / `--memory-graph`.

`generation/offline_answer.py` + `offline_heuristics/rules_*.py` are a rule set hardcoded to the demo corpus — they make `--no-llm` evals deterministic, are excluded from coverage, and are **not** the production path. Don't "fix" live-LLM behavior by editing them, and vice versa.

### Composition root and protocols

`stores/factory.py` is the single composition root: `create_offline_bundle()` / `create_live_bundle()` return a `StoreBundle` (graph/vector/fulltext/docs). Application code depends only on the protocols in `stores/interfaces.py` — never on Neo4j/Qdrant client types. Live backends are imported lazily inside the factory.

### Query flow

`api/routes/query.py` → `QueryService` (`api/service.py`, holds bundle + audit store + review queue + retrieval cache + multi-tenant budget) → `agent/loop.run_query()`:

1. **Chitchat short-circuit** (`agent/chitchat.py`) runs *before* triage: greetings/capability questions skip retrieval entirely, even under `force_agentic`.
2. **Triage** (`agent/triage.py`) routes to Fast Path or Agentic; Fast Path (`agent/fast_path.py`) can escalate back on weak evidence.
3. **Agentic** = LangGraph `StateGraph`: `planner → executor → critic → (loop | answer)`, compiled with a checkpointer. `loop.py` only wires the graph; handlers live in `loop_runtime.py`/`loop_handlers.py`. Planner emits a DAG (`plan_dag.py`) that can gain sub-questions mid-run.
4. **Executor** fans out to three retrievers — vector, graph beam search, BM25 — fused via RRF (`retrieval/fusion.py`), through `RetrievalCache` (invalidated by index version).
5. **Guardrails** (`agent/guardrails.py`): max hops, max LLM calls, token budget (tenant-level in `budget_policy.py`), timeout, recursion limit; `llm/circuit.py` is a circuit breaker on the live LLM client.

**All three retrievers return the same `Candidate` shape** (`retrieval/contracts.py`) — fusion, agent memory, citation binding, and eval evidence-recall consume it. Adding a retriever means conforming to that contract.

The output contract is `ReasoningChain` (`generation/trace.py`); its JSON Schema is `configs/schema/reasoning_chain_v1.json` (regenerate with `export-reasoning-schema`). Chains persist to the audit store; `POST /v1/feedback` links feedback to a chain and enqueues inaccurate ones into the review queue.

### Knowledge pipeline (`knowledge/`)

`ingest.py` → `extraction.py` (per-chunk LLM extraction with resume journal, retry, quarantine) → `schema_check.py` (validates against `configs/schema/domain_v0.yaml` + confidence threshold; **non-conforming triples are rejected, never entering the graph**) → `resolution.py`/`resolution_merge.py` (entity disambiguation) → `graph_builder.py`.

`incremental.py` handles new docs: extract → conflict detect → high-confidence auto-merge, low-confidence into the review queue (`knowledge/review/queue.py`). `pilot_triples.py` mirrors `scripts/generate_pilot_corpus.py` — both must be changed together. `ingest_tasks.py` is the durable upload task state machine consumed by `ingest_worker.py`.

### API surface

All routers are `prefix="/v1"`, wrapped by `AuthRateLimitMiddleware` (`api/auth.py`); role checks via `Depends(require_role(...))` from `api/rbac.py`; responses use the shared envelope (`api/envelope.py`, `api/errors.py`).

- **query.py**: `POST /v1/query`, `POST /v1/query/stream` (SSE)
- **knowledge.py**: `POST /v1/docs` (admin/operator), `GET /v1/ingest-tasks/{id}`, `GET /v1/review-queue`, `POST /v1/review-queue/{id}/decision` (admin/operator), `GET /v1/audit/queries/{id}`, `POST /v1/feedback`, `GET /v1/metrics` (admin), `GET /v1/graph/entities`. Upload/ingest-task helpers live in `knowledge_upload.py`, imported by `knowledge.py`.
- **admin.py** (admin-only): `GET /v1/traces/{id}`, `GET /v1/budget/snapshot`, `GET /v1/audit-events`
- **app.py**: `GET /healthz`, `GET /metrics-prom`, `GET /web` (trial UI)

`QueryService` is deliberately split across `service*.py` for the file-size gate.

### Config

`config.py` merges `configs/default.yaml` (`AppConfig`; `tenants:`/`retention:` in `config_enterprise.py`) with `.env` (`Settings`); env overrides YAML. Repo root is auto-discovered (`AGENTIC_GRAPHRAG_ROOT` to override); all data paths resolve via `resolve_path()`. LLM prompts are markdown in `configs/prompts/` loaded by `load_prompt(name)`.

`AGR_*` switches are read from `os.environ` directly (the **only** sanctioned direct-environ reads — new settings belong in `AppConfig`/`Settings`): `AGR_ALLOW_LLM`, `AGR_USE_LIVE_STORES`, `AGR_REQUIRE_AUTH`, `AGR_API_KEYS=tenant:key:role,...` (roles admin/operator/reader; bare `tenant:key` = reader), `AGR_RATE_LIMIT_QPS`, `AGR_RATE_LIMIT_CONCURRENT`, `AGR_TRUST_X_USER_ID`, `AGR_LOG_LEVEL/FILE`, `AGR_REDACTION_ENABLED/PATTERNS`, `AGR_OTEL_ENABLED/ENDPOINT/SAMPLE_RATE`, `AGR_API_HOST/PORT/RELOAD`.

### Enterprise layer (ENT-01…08)

Observability in `observability/`: JSON logging (`logging_setup.py`), audit event JSONL (`audit_events.py`), metrics (`metrics.py`; Prometheus text via `prometheus_metrics_text()` in `api/routes/admin.py`), PII redaction (default off), optional OTel via `.[otel]` extra (coverage-omitted). `tenant_id` threads from `Principal` through all stores, retrievers, and the agent loop (tenant-scoped runs bypass the retrieval cache). Uploads are governed (5MB/20 files/md|txt|pdf) into a durable `IngestTaskStore` consumed by a separately-run `ingest_worker`. ENT-07 (RPA/webhooks) is explicitly out of scope. Status: `docs/ENTERPRISE_READINESS.md` §3.5; ops: `docs/ops-runbook.md`.

### Evaluation and gates

Datasets in `evals/datasets/*.jsonl`; gold cases generated deterministically from templates (`eval/gold_gen.py`) by walking the graph — no LLM. `run-cases` writes `reports/*.jsonl` + accuracy; `run-baseline` runs the pure-vector RAG baseline for a fair delta. Metrics (`eval/metrics.py`): accuracy, evidence recall, latency, cost, fabrication rate.

`./scripts/verify_all.sh` is the master pack, reporting two **separate** verdicts: `engineering_pass` (offline suite) and `formal_pass` (product/live AC gates — expected `false` until a real authorized domain, live held-out evidence, human gold sign-off and staging P95 exist). Offline results never set `formal_pass`.

## Conventions

- **Binding rules live in `plan/engineering/rules.md`** with their enforcement mechanism. Design-vs-implementation divergences are marked "⚠ 差异" and ledgered in `docs/IMPORTANT.md`.
- **Architecture boundaries (review-enforced).** LangGraph is confined to `agent/`; do **not** introduce LangChain retrieval/chain abstractions — retrieval and LLM calls go through this repo's `retrieval/` and `llm/` interfaces. Graph nodes are "state in → state out" so unit tests never need the LangGraph runtime. New features must run under `--no-llm` + in-memory backends or explicitly declare themselves live-only.
- **`docs/IMPORTANT.md` is the debt/deferral ledger.** When you close or defer a task, update it *in the same change set* (plus the phase checklist in `plan/phases/` and any gate JSON / risk status). Task status markers: `[ ]` not started, `[~]` in progress, `[x]` done, `[-]` cancelled. Task IDs like `P2-KG-01`, `C1/C2/C3` refer to `plan/` phase files.
- **Changing a technology choice requires a new ADR in `plan/engineering/tech-stack.md` first.**
- **Engineering-done ≠ product-accepted.** Gates G1–G4 (`plan/roadmap.md`) require live-LLM/held-out evidence; offline synthetic results must not be presented as gate evidence.
- **Hard code metrics are enforced by `scripts/check_code_metrics.py`** — this is why modules are deliberately split. New modules target ~200–400 lines; put constants in module-level named constants.
- **Coverage omit list in `pyproject.toml` is intentional** (CLI glue, `stores/neo4j_store.py`, live LLM client, `generation/answer.py`, offline heuristic `rules_*.py`, `otel_bridge.py`). Don't silently expand it.
- **Trial UI (`web/`)**: zero-build — no npm/bundler; Vue 3 ESM runtime only, vendored-first and pinned. Dynamic text goes through mustache or `textContent`; `v-html`/`.innerHTML` forbidden. JS/CSS/HTML obey the same size/complexity limits as Python (review-enforced). SSE consumers must handle every event type (`cache_hit/triage/thinking/sub_question/hop_done/answer/error`) and silently ignore unknown ones. DOM/module changes must update assertions in `tests/unit/test_web_claude_ui.py`.
- Ruff: line length 100, target py312, rules `E,F,I,UP,B`. Tests: pytest with `asyncio_mode = "auto"`.
- Some environments here have no Docker: Neo4j runs from a tarball under `/tmp` (see `docs/EXTERNAL_RUNTIMES.md`), and the LLM gateway is prone to 403s — prefer offline paths unless the task requires live backends.
