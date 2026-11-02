#!/usr/bin/env bash
# 离线演示数据引导：ingest → seed 图 → BM25 索引（不调用 LLM，不需要 Docker）。
# 由 ./scripts/setup.sh --with-demo-data 调用，也可单独执行（可重复运行）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV_PY="$ROOT/.venv/bin/python"
if [[ -x "$VENV_PY" ]]; then
  PY="$VENV_PY"
else
  PY="$(command -v python3)"
  echo "提示: 未发现 .venv，使用 $PY；建议先运行 ./scripts/setup.sh" >&2
fi
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

# 优先用已安装的控制台入口，缺失时回退到模块调用。
agr() {
  local cmd="$1"
  shift
  if [[ -x "$ROOT/.venv/bin/agr-$cmd" ]]; then
    "$ROOT/.venv/bin/agr-$cmd" "$@"
  else
    "$PY" -m agentic_graphrag "$cmd" "$@"
  fi
}

echo "==> 切分语料（ingest）"
agr ingest

echo "==> 载入 seed 三元组（内存图，无 LLM）"
agr build-graph --triples data/processed/seed_triples.jsonl --no-llm
echo "    （需要 Neo4j：先 docker compose up -d；强制内存图：加 --memory-graph）"

echo "==> 构建 BM25 索引（不调用 embedding）"
agr index --no-embed

cat <<'MSG'

完成。下一步
  agr-query --no-llm "Who is the CEO of the parent company of NovaTech Industries?"
  agr-run-cases --no-llm --cases evals/datasets/poc_cases.jsonl
MSG
