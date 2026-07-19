#!/usr/bin/env bash
# Bootstrap offline POC: ingest → seed graph → BM25 index
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Ingest documents"
python -c "from agentic_graphrag.cli import ingest_main; ingest_main([])"

echo "==> Load seed triples into Neo4j (requires docker compose up)"
python -c "from agentic_graphrag.cli import build_graph_main; build_graph_main(['--triples','data/processed/seed_triples.jsonl','--no-llm'])"

echo "==> Build BM25 index"
python -c "from agentic_graphrag.cli import index_main; index_main(['--no-embed'])"

echo "Done. Run: pytest -q"
echo "Then: python -c \"from agentic_graphrag.cli import run_cases_main; run_cases_main(['--no-llm'])\""
