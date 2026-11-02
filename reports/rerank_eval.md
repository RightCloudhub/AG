# Rerank evaluation — 2026-11-03

**Decision: keep `retrieval.rerank.kind: none` for live/hybrid and offline.** No TEI endpoint or live P95 budget was available (confirmed by the user). Current cross-encoder accuracy, latency and cost are unmeasured; no cross-encoder default promotion is justified.

## Controlled offline sweep

Existing `g2_all.jsonl` (200 cases), 519 pilot triples and 226 pilot chunks; the same in-memory graph, BM25 index and deterministic hash embeddings for every variant. Answers use the existing offline heuristics. CPU: AMD Ryzen 5 6600H; no model inference/GPU. This measures retrieval sensitivity and implementation overhead, not live answer quality.

The experiment uses `cli.cases_run.run_case_row`, the production executor/pipeline, and the existing scorer/McNemar comparison. It explicitly enables experimental rerankers with mock generation: production `--no-llm` still disables all rerankers. All three retrieval channels use pool 10/20/30 and RRF k=60, then retain exactly 10 candidates. `none` has the same pool and final cut as its paired lexical variant. Cross-encoder graph policy would be `exempt`; lexical uses token coverage plus its existing RRF prior.

**Recall@10** is the macro-average fraction of gold evidence/path items found in the retained top-10 evidence of a separate original-question retrieval. It uses the existing evidence matcher (including relation aliases), excludes answer text and pre-cut tool traces, and is not exact chunk-ID recall. Answer runs use fresh executors and are timed separately; recall probes cannot prewarm their score cache. The denominator is 180 cases with gold evidence.

| Reranker | Pool | Accuracy | Evidence recall@10 | Query P50 / P95 ms | Added P50 / P95 ms¹ | External LLM cost |
|---|---:|---:|---:|---:|---:|---|
| none | 10 | 185/200 (92.5%) | 92.66% | 42.43 / 72.67 | +0.00 / +0.00 | $0; 0 calls/tokens |
| lexical | 10 | 190/200 (95.0%) | 91.47% | 44.09 / 73.77 | +2.18 / +16.16 | $0; 0 calls/tokens |
| cross-encoder HTTP | 10 | Unmeasured | Unmeasured | Unmeasured | Unmeasured | Unmeasured |
| none | 20 | 185/200 (92.5%) | 92.66% | 45.23 / 80.30 | +0.00 / +0.00 | $0; 0 calls/tokens |
| lexical | 20 | 190/200 (95.0%) | 91.32% | 44.97 / 68.80 | -0.80 / +15.49 | $0; 0 calls/tokens |
| cross-encoder HTTP | 20 | Unmeasured | Unmeasured | Unmeasured | Unmeasured | Unmeasured |
| none | 30 | 185/200 (92.5%) | 92.47% | 45.57 / 69.70 | +0.00 / +0.00 | $0; 0 calls/tokens |
| lexical | 30 | 190/200 (95.0%) | 91.32% | 46.01 / 78.16 | -0.25 / +16.74 | $0; 0 calls/tokens |
| cross-encoder HTTP | 30 | Unmeasured | Unmeasured | Unmeasured | Unmeasured | Unmeasured |

¹ Percentiles of the **per-case paired latency differences** against `none` at the same pool, not a difference of aggregate percentiles. Single pass per variant, one warm-up case outside timing, variants run sequentially. Negative medians reflect host/runtime variation; they do not demonstrate a speedup. CPU/hosting costs are not priced. Fake HTTP tests below establish behavior only and supply no model performance numbers.

| Lexical pool | Reranking stage P50 / P95 ms | McNemar (candidate-only / base-only) | p |
|---|---:|---:|---:|
| 10 | 0.88 / 1.66 | 5 / 0 | 0.0625 |
| 20 | 1.48 / 2.78 | 5 / 0 | 0.0625 |
| 30 | 1.94 / 3.62 | 5 / 0 | 0.0625 |

Lexical gained five answers at every pool, but p=0.0625 exceeds the repository’s p<0.05 criterion. Recall fell by about 1.1–1.3 percentage points. Pool 20/30 offered no accuracy improvement over 10 in this fixture. All six measured variants had zero execution errors and zero external LLM calls/tokens. These results do not establish a benefit for cross-encoder reranking.

The sweep baseline (185/200) differs from the normal offline regression because this experiment adds pilot text/vector indexes and forces a final top-10 cut. The existing offline profile uses its original retrieval settings and scores **200/200**; its defaults were not changed.

## Reliability and standard offline regression

Offline monkeypatched HTTP tests cover reordering, timeout, 400, one retry on 500/503, malformed/incomplete/duplicate/non-finite scores, failure after a successful batch, input truncation without evidence mutation, graph exemption/floor, breaker cooldown/recovery across requests, shared chunk scores across parallel branches/register calls, and cache reset between queries. The optional local adapter is tested with a fake sentence-transformers model; real local inference is unmeasured.

The standard case runner was executed with `AGR_RERANK=http --no-llm --memory-graph`: POC agentic **31/31**, POC triage **31/31**, G2 **200/200**, compositional **54/54**, negative **20/20**. No HTTP/model dependency was used. A paired G2 run against the unmodified base commit `844d2b0` also scored 200/200: zero correctness flips, McNemar p=1.0, P95 62 → 56 ms (single-run noise; no speedup claim). The HTTP/local adapters remain opt-in and degraded answers are excluded from the answer cache.

TEI Compose YAML was parsed and reviewed. Docker is absent here, so image startup, model download and actual `/rerank` inference were not validated. The optional dependency is in `uv.lock`; the default dev/webapp environment does not install or import sentence-transformers.

## Validation

- `ruff check src tests scripts`: passed.
- `ruff format --check src tests scripts`: passed (415 files).
- `mypy`: passed (298 source files).
- `pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q`: **656 passed, 89.74% coverage**. One existing Starlette/httpx deprecation warning.
- `python scripts/check_code_metrics.py`: passed.
- `uv lock --check --offline` and `git diff --check`: passed.

## Reproduce and promote

```bash
PYTHONPATH=src .venv/bin/python scripts/rerank_eval.py --out reports/rerank_runs
PYTHONPATH=src .venv/bin/python scripts/eval_compare.py \
  reports/rerank_runs/none_30.jsonl reports/rerank_runs/lexical_30.jsonl \
  --cases evals/datasets/g2_all.jsonl
# Only after a real TEI service is available:
PYTHONPATH=src .venv/bin/python scripts/rerank_eval.py \
  --endpoint http://127.0.0.1:8081/rerank --out reports/rerank_tei_runs
```

Raw case JSONL files are generated under `reports/rerank_runs/` (ignored runtime artifacts). The saved [summary](rerank_eval_summary.json) includes fixture SHA-256 hashes, exact metrics and paired counts. The script’s HTTP mode still uses mock generation/embeddings; it is a model/retrieval probe, not live promotion evidence.

Before switching live defaults: agree on a P95 increase budget, fix the TEI model revision and hardware, run the existing case evaluator with the actual live stores/embeddings/LLM and identical LLM cache for none/lexical/http at all three pools, record observed HTTP scoring work and hosting/API cost, exclude degraded runs, and require higher accuracy with paired p<0.05 and acceptable P95. Retest graph score/exempt/floor on that corpus. No numeric acceptance budget has been invented for this report.

Historical context only: [ARCH_OPTIMIZATION_V2 §7](../docs/design/ARCH_OPTIMIZATION_V2.md) reports an older MiniLM CPU experiment with no significant accuracy gain and 0.4–0.8 s extra P95. Different model and code state; those numbers are not substituted into this matrix.
