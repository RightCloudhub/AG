# Baseline vector RAG (P2-EV-03)

Pure **vector** retrieval + single-shot generation. No graph multi-hop, no agent loop.

## Run on interim / temporary data

```bash
# Offline (Mock embeddings + extractive answer) — uses data/processed/chunks.jsonl
# or falls back to chunking data/raw/
python -m agentic_graphrag run-baseline --no-llm

# Explicit paths
python -m agentic_graphrag run-baseline --no-llm \
  --cases evals/datasets/poc_cases.jsonl \
  --raw-docs data/raw \
  --out reports/

# Entry point
agr-run-baseline --no-llm
```

Outputs:

- `reports/baseline_run.jsonl` — per-case predictions (route=`baseline`)
- `reports/baseline_accuracy.json` — token-overlap accuracy vs gold

Fair comparison with agentic: same cases file, same `score` helpers.
Live LLM mode (omit `--no-llm` when `LLM_API_KEY` is set) uses the same strong model tier as agentic generation.
