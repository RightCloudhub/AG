#!/usr/bin/env bash
# G2 formal heldout agentic vs baseline (offline by default; --with-llm for live).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

WITH_LLM=0
for arg in "$@"; do
  case "$arg" in
    --with-llm) WITH_LLM=1 ;;
    -h|--help)
      echo "Usage: $0 [--with-llm]"
      exit 0
      ;;
  esac
done

PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

CASES="evals/datasets/g2_heldout.jsonl"
SEED="data/processed/pilot_triples.jsonl"
CHUNKS="data/processed/pilot_chunks.jsonl"
MODE="offline_no_llm"
OUT="reports/g2_heldout"
LLM_FLAGS=(--no-llm --memory-graph)
if [[ "$WITH_LLM" -eq 1 ]]; then
  if [[ -z "${LLM_API_KEY:-}" || "$LLM_API_KEY" == *"your-key"* ]]; then
    echo "LLM_API_KEY missing — skip live heldout (blocked_llm)"
    mkdir -p reports
    cat > reports/g2_heldout_live_status.json <<'JSON'
{"status":"blocked_llm","reason":"LLM_API_KEY not configured"}
JSON
    exit 0
  fi
  MODE="live_llm"
  OUT="reports/g2_heldout_live"
  LLM_FLAGS=(--memory-graph --force-agentic)
fi

mkdir -p "$OUT"

echo "=== G2 formal heldout ($MODE) ==="
"$PY" -m agentic_graphrag run-cases \
  "${LLM_FLAGS[@]}" \
  --cases "$CASES" \
  --seed-triples "$SEED" \
  --out "$OUT" \
  --run-name agentic_run \
  --force-agentic

BASE_FLAGS=(--cases "$CASES" --chunks "$CHUNKS" --out "$OUT")
if [[ "$WITH_LLM" -eq 0 ]]; then
  BASE_FLAGS=(--no-llm "${BASE_FLAGS[@]}")
fi
"$PY" -m agentic_graphrag run-baseline "${BASE_FLAGS[@]}"

"$PY" -m agentic_graphrag eval \
  --agentic "$OUT/agentic_run.jsonl" \
  --baseline "$OUT/baseline_run.jsonl" \
  --cases "$CASES" \
  --out "$OUT" \
  --stem "g2_heldout_eval"

"$PY" -m agentic_graphrag badcase \
  --run "$OUT/agentic_run.jsonl" \
  --cases "$CASES" \
  --out "$OUT/g2_heldout_badcase.json" || true

# Lock memo
{
  echo "# G2 heldout formal pack"
  echo
  echo "- mode: \`$MODE\`"
  echo "- cases: \`$CASES\`"
  echo "- seed: \`$SEED\`"
  echo "- out: \`$OUT\`"
  echo "- note: heldout is gate-only; do not tune prompts on this split (R7)."
  if [[ -f "$OUT/g2_heldout_eval.json" ]]; then
    echo
    echo "See \`$OUT/g2_heldout_eval.json\` for Accuracy Δpp and evidence recall."
  fi
} > "$OUT/G2_effect.md"

echo "Wrote $OUT"
