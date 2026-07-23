#!/usr/bin/env bash
# G2 formal heldout: agentic vs baseline (offline default; --with-llm for live).
#
# Usage (from repo root):
#   ./scripts/g2_formal_eval.sh
#   ./scripts/g2_formal_eval.sh --with-llm
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Load repo .env (LLM_API_KEY, LLM_BASE_URL, Neo4j/Qdrant, …). Existing shell
# exports still win for already-set variables if .env uses plain assignments.
if [[ -f "${ROOT}/.env" ]]; then
  # shellcheck disable=SC1091
  set -a
  # shellcheck source=/dev/null
  source "${ROOT}/.env"
  set +a
fi

WITH_LLM=0
WITH_TRIAGE=0
for arg in "$@"; do
  case "$arg" in
    --with-llm) WITH_LLM=1 ;;
    --with-triage) WITH_TRIAGE=1 ;;
    -h|--help)
      echo "Usage: $0 [--with-llm] [--with-triage]"
      echo "  Offline (default): --no-llm heldout agentic + baseline + eval"
      echo "  --with-llm: reads LLM_API_KEY from .env; → reports/g2_heldout_live/"
      echo "  --with-triage: enable Fast Path triage (better P95 mix; not force_agentic)"
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

_llm_key_ok() {
  local k="${LLM_API_KEY:-}"
  [[ -n "$k" ]] || return 1
  [[ "$k" != "sk-your-key" ]] || return 1
  [[ "$k" != *"your-key"* ]] || return 1
  return 0
}

if [[ "$WITH_LLM" -eq 1 ]]; then
  if ! _llm_key_ok; then
    echo "LLM_API_KEY missing or placeholder in .env / environment — skip live (blocked_llm)"
    echo "Set LLM_API_KEY in ${ROOT}/.env (see .env.example)"
    mkdir -p reports
    cat > reports/g2_heldout_live_status.json <<'JSON'
{"status":"blocked_llm","reason":"LLM_API_KEY not configured in .env or environment"}
JSON
    echo "Wrote reports/g2_heldout_live_status.json"
    exit 0
  fi
  MODE="live_llm"
  OUT="reports/g2_heldout_live"
  if [[ "$WITH_TRIAGE" -eq 1 ]]; then
    LLM_FLAGS=(--memory-graph --enable-triage)
    MODE="live_llm_triage"
    OUT="reports/g2_heldout_live_triage"
  else
    LLM_FLAGS=(--memory-graph --force-agentic)
  fi
  echo "Using LLM from .env/env (base=${LLM_BASE_URL:-default})"
fi

if [[ ! -f "$CASES" ]]; then
  echo "Missing cases: $CASES" >&2
  echo "Generate first: $PY -m agentic_graphrag gen-cases" >&2
  exit 1
fi
if [[ ! -f "$SEED" ]]; then
  echo "Missing seed triples: $SEED" >&2
  exit 1
fi

mkdir -p "$OUT" reports

echo "=== G2 formal heldout ($MODE) → $OUT ==="
RUN_EXTRA=()
if [[ "$WITH_TRIAGE" -eq 0 ]]; then
  # Accuracy gate: force full agentic. P95 mix: pass --with-triage instead.
  RUN_EXTRA+=(--force-agentic)
fi
"$PY" -m agentic_graphrag run-cases \
  "${LLM_FLAGS[@]}" \
  --cases "$CASES" \
  --seed-triples "$SEED" \
  --out "$OUT" \
  --run-name agentic_run \
  "${RUN_EXTRA[@]}"

BASE_FLAGS=(--cases "$CASES" --chunks "$CHUNKS" --out "$OUT")
if [[ "$WITH_LLM" -eq 0 ]]; then
  BASE_FLAGS=(--no-llm "${BASE_FLAGS[@]}")
fi
if [[ ! -f "$CHUNKS" ]]; then
  echo "WARN: missing $CHUNKS — baseline may fail; try pilot ingest first" >&2
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

echo "Wrote $OUT (mode=$MODE)"
