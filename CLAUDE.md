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

# Lint / format (CI-enforced)
ruff check src tests scripts
ruff format --check src tests scripts

# Tests (CI gate: unit tests + coverage ≥80%)
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

# Infra (optional; only live paths need it)
docker compose up -d                          # Neo4j 7474/7687 (neo4j/agentic-graphrag), Qdrant 6333

# Gate / regression scripts
./scripts/g1_to_g2_gate.sh                    # C1/C2/C3 summary (--with-llm for live)
PYTHONPATH=src .venv/bin/python scripts/p3_ev_offline.py
```

All subcommands are also reachable as `python -m agentic_graphrag <command>` (ingest, build-graph, index, run-cases, run-baseline, score, eval, gen-cases, pilot-triples, badcase, spotcheck, score-spotcheck, export-reasoning-schema, query). `pyproject.toml` sets `pythonpath = ["src"]` for pytest, so tests run without install; scripts use `PYTHONPATH=src`.

## Architecture

### The offline/live duality (read this first)

Every layer has two implementations, and the offline one is the default everywhere. This is what keeps CI and the 20-case eval deterministic:

| Layer | Offline (default) | Live (opt-in) |
|---|---|---|
| Graph | `InMemoryGraphStore` + `data/processed/seed_triples.jsonl` | Neo4j (`--neo4j`, or live bundle) |
| Vector | in-memory + persisted embeddings if present | Qdrant |
| LLM | `MockLLMProvider` + offline answer heuristics | real provider (OpenAI-compatible) |
| API | `QueryService.create_offline()` in FastAPI lifespan | `AGR_ALLOW_LLM=1` + real `LLM_API_KEY` |

The offline answer path (`generation/offline_answer.py` + `generation/offline_heuristics/`) is a large rule set hardcoded to the demo corpus in `data/raw/` — it exists to make `--no-llm` evals deterministic, is excluded from coverage, and is **not** the production path. Don't "fix" live-LLM behavior by editing it, and vice versa.

### Composition root and protocols

`stores/factory.py` is the single composition root: `create_offline_bundle()` / `create_live_bundle()` return a `StoreBundle` (graph/vector/fulltext/docs). Application code depends only on the protocols in `stores/interfaces.py` (`GraphStore`, `VectorStore`, `FulltextStore`, `DocStore`) — never on Neo4j/Qdrant client types. Live backends are imported lazily inside the factory so offline paths don't need those deps at runtime.

### Query flow

`api/routes/query.py` → `QueryService` (`api/service.py`, holds the bundle + audit store + review queue + retrieval cache + multi-tenant budget) → `agent/loop.run_query()`:

1. **Triage** (`agent/triage.py`) routes to Fast Path or Agentic; Fast Path (`agent/fast_path.py`) can escalate back to Agentic on weak evidence (`should_escalate_fast_path`).
2. **Agentic** = LangGraph `StateGraph`: `planner → executor → critic → (loop back to executor | answer)`, compiled with a checkpointer (`agent/checkpointer.py`) for durable state. Node handlers live in `agent/loop_runtime.py`/`loop_handlers.py`; `loop.py` only wires the graph.
3. **Executor** (`agent/executor.py` + `executor_plan.py`/`executor_dispatch.py`) picks tools and fans out to three retrievers — vector, graph beam search, BM25 (`retrieval/`) — fused via RRF (`retrieval/fusion.py`), with `RetrievalCache`.
4. **Guardrails** (`agent/guardrails.py`, config `guardrails:` section): max hops, max LLM calls, token budget (`llm/budget.py`, tenant-level in `budget_policy.py`), timeout, recursion limit.

The output contract is `ReasoningChain` (`generation/trace.py`); its JSON Schema is `configs/schema/reasoning_chain_v1.json` (regenerate with `export-reasoning-schema`). Chains are persisted to the audit store; `POST /v1/feedback` links user feedback to a chain and enqueues inaccurate ones into the review queue.

### Config

`config.py` merges `configs/default.yaml` (`AppConfig`, tunables) with `.env` (`Settings`, secrets/endpoints via pydantic-settings); env overrides YAML. Repo root is auto-discovered (`AGENTIC_GRAPHRAG_ROOT` to override), and all data paths resolve against it via `resolve_path()` regardless of cwd. LLM prompts are markdown files in `configs/prompts/` loaded by `load_prompt(name)`. API auth/rate limiting: `AGR_REQUIRE_AUTH=1`, `AGR_API_KEYS=tenant:key,...`, `AGR_RATE_LIMIT_QPS`.

### Evaluation

Datasets live in `evals/datasets/*.jsonl` (poc, dev/heldout/guardrail splits, g2_* generated gold sets; spec in `ANNOTATION_SPEC.md`). `run-cases` writes `reports/*.jsonl` + accuracy JSON; `run-baseline` runs the pure-vector RAG baseline for comparison; `badcase` does attribution. Gold cases are generated deterministically from templates (`eval/gold_templates/`).

## Conventions

- **Binding rules are consolidated in `plan/engineering/rules.md`** (code metrics, security checklist, architecture boundaries, frontend zero-build rule, docs-sync duties) with their enforcement mechanism (CI / gate script / review). Design-vs-implementation divergences are marked "⚠ 差异" in workstream docs and ledgered in `docs/IMPORTANT.md`. SSE is true incremental via LangGraph `stream(updates)` (`agent/loop_stream.py` + `api/service_stream.py`).
- **`docs/IMPORTANT.md` is the debt/deferral ledger.** Every intentionally deferred, blocked, or not-doing item lives there. When you close or defer a task, update it (and the phase checklist in `plan/phases/`). Task IDs like `P2-KG-01`, `P3-PERF-06`, `C1/C2/C3` refer to `plan/` phase files.
- **Engineering-done ≠ product-accepted.** Gates G1–G4 (`plan/roadmap.md`) require live-LLM/held-out evidence; offline synthetic results must not be presented as gate evidence. README's status table reflects this split — keep it honest when updating.
- **Hard code metrics are enforced by `scripts/check_code_metrics.py`** (same limits as the table above). This is why modules are deliberately split (`loop` / `loop_handlers` / `loop_runtime`, `executor` / `executor_plan` / `executor_dispatch`, `service` / `service_helpers` / `service_query`). New modules target ~200–400 lines; put constants in module-level named constants, not inline literals.
- **Coverage omit list in `pyproject.toml` is intentional** (CLI glue, Neo4j/Qdrant live adapters, live LLM client, offline heuristics). If you add code to an omitted module, either cover it or keep the omission justified; don't silently expand the list.
- **Frontend (web/) rules** (per `plan/engineering/rules.md` §8 V1.1): zero-build with no toolchain; frontend whitelist is **pinned Vue 3 only** (ADR-006, ESM runtime, vendor-first + CDN fallback). Dynamic text must use mustache/`textContent` — **no `v-html` or `innerHTML`**. JS/HTML modules follow the same ≤300-line limit as Python (review-enforced).
- Ruff: line length 100, target py312, rules `E,F,I,UP,B`. Tests: pytest with `asyncio_mode = "auto"`.
- Some environments here have no Docker: Neo4j runs from a tarball under `/tmp` with a Temurin 17 `JAVA_HOME` (see `docs/EXTERNAL_RUNTIMES.md`), and the LLM gateway is prone to 403s — prefer offline paths unless the task requires live backends.
