# refactor: carry typed models through AgentState

Agent nodes previously validated and dumped the reasoning chain, evidence, register,
and memory on every hop. AgentState v3 now carries `ReasoningChain`, an id-keyed
`dict[str, Candidate]`, `Register`, and `MemoryState` directly. `RegisterRuntimeState`
groups the nine auxiliary register channels; typed subquestions and prefetched
candidates/tool traces also stay as objects between nodes.

Evidence and register events have explicit reducers. Nodes copy mutable state before
updating it, preserving earlier checkpoints and emitted steps. The executor applies
its complete evidence pool with LangGraph `Overwrite`, preserving legacy seed
replacement, empty-result, and duplicate-id behavior. Fresh queries reset append
channels when a caller reuses a thread id.

State serialization runs at checkpoint, SSE, and API output boundaries. Default
checkpoint codecs serialize model containers in bulk; caller-supplied serializers
are preserved. LLM prompt formatting remains separate from state hydration.

## Four-hop benchmark — 2026-11-03

Baseline was measured before editing state/nodes, with state v2 at `844d2b0`.
Both runs used Python 3.12.13, Pydantic 2.13.4, LangGraph 1.2.9, in-memory stores
and checkpoints, pilot triples, disabled answer/retrieval caches, and no LLM.
Store/executor construction and five warmups are excluded. Each measurement has
50 iterations and asserts that executed hops are exactly `[1, 2, 3, 4]`.

Question: “Who is the CEO of the parent company of the competitor of the producer
of Frost Lakehouse?”

| Measurement per query | Before: v2 | After: v3 |
|---|---:|---:|
| `model_validate` calls | 515 | 0 |
| `model_dump` calls | 445 | 1 |
| `model_validate` time, ms | 5.727696 | 0.000000 |
| `model_dump` time, ms | 7.014198 | 0.039478 |
| Combined public-method time, ms | **12.741893** | **0.039478** |
| Uninstrumented query wall median, ms | **82.190335** | **76.711348** |

Combined public-method time fell **99.69%**; query wall median fell **6.67%**.
Method timings cover checkpoint worker threads and are averaged per query.
The public-method timer excludes bulk `TypeAdapter` codecs; the separate
uninstrumented wall measurement includes checkpoint serialization and other query
work. The remaining public dump occurs at the checkpoint boundary.

```bash
PYTHONPATH=src .venv/bin/python scripts/benchmark_agent_state.py \
  --iterations 50 --warmup 5 --output /tmp/typed-state-benchmark-after.json
```

Raw session artifacts: `/tmp/typed-state-benchmark-before.json` and
`/tmp/typed-state-benchmark-after.json`.

## Validation

All commands passed using the existing `/home/arch1/AG/.venv` environment:

```text
ruff check src tests scripts
ruff format --check src tests scripts
mypy
pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q
python scripts/check_code_metrics.py
git diff --check
```

- Mypy: no issues in all 298 source modules; no added `Any` or `type: ignore`.
- Unit suite: **651 passed**, coverage **89.74%** (baseline: 627 passed, 89.47%).
- Added 24 regression cases covering typed object reuse, checkpoint isolation,
  parallel reducers, event replay, prefetch, thread reuse, legacy resume, pending
  writes, current input snapshots, codecs, async history, and custom serializers.
- No `model_validate` matches in agent node/handler modules for chain, evidence,
  or register. Memory snapshot hydration is confined to the migration boundary.
- Offline answer rules and heuristics have no diff.

| Offline suite | Before | After |
|---|---:|---:|
| POC | 31/31 | 31/31 |
| POC with triage | 31/31 | 31/31 |
| G2 | 200/200 | 200/200 |
| Compositional | 54/54 | 54/54 |
| Negative | 20/20 | 20/20 |

All **336 complete report rows** matched after recursively excluding only
`query_id`, `thread_id`, and `latency_ms`; complete accuracy reports matched too.
The unchanged production evaluation driver ran each suite with its original
dataset and seed/pilot graph into isolated directories. Paired G2 comparison:
zero discordant outcomes, exact McNemar **p=1.0000**, P95 latency **63 → 59 ms**,
and mean LLM calls **0 → 0**.

```bash
PYTHONPATH=src .venv/bin/python scripts/eval_compare.py \
  /tmp/typed-state-evals/baseline/g2/g2.jsonl \
  /tmp/typed-state-evals/after/g2/g2.jsonl \
  --cases evals/datasets/g2_all.jsonl --fail-on-worse
```

## Migration and documentation

Writers emit state v3. `StateCheckpointer` migrates versionless/v1/v2 snapshots on
read, including renamed channels, channel versions, `versions_seen`, and pending
writes. Current-version input snapshots also hydrate models while preserving
`Overwrite` commands. Unknown future versions are rejected. Memory and optional
SQLite backends use the same synchronous/asynchronous migration adapter.

Updated [ARCHITECTURE.md §2](../ARCHITECTURE.md#2-agent-编排-agent) and appended the
2026-11-03 completion entry to [IMPORTANT.md](./IMPORTANT.md).
