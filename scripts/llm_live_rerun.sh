#!/usr/bin/env bash
# C2 — Live LLM re-run: extract (+ spotcheck template) + 20 cases
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

SKIP_EXTRACT=0
SKIP_CASES=0
for arg in "$@"; do
  case "$arg" in
    --skip-extract) SKIP_EXTRACT=1 ;;
    --skip-cases) SKIP_CASES=1 ;;
    -h|--help)
      echo "Usage: $0 [--skip-extract] [--skip-cases]"
      echo "Requires LLM_API_KEY in environment or .env"
      exit 0
      ;;
  esac
done

echo "=== C2 Live LLM re-run ==="

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a
  source .env
  set +a
fi

if [[ -z "${LLM_API_KEY:-}" || "$LLM_API_KEY" == "sk-your-key" ]]; then
  echo "FAIL: set LLM_API_KEY in .env (see .env.example)"
  exit 2
fi

mkdir -p reports data/processed

# Prefer pilot raw if populated, else interim data/raw
INPUT="data/raw"
if [[ -d data/pilot/raw ]]; then
  N="$(find data/pilot/raw -type f \( -name '*.md' -o -name '*.txt' -o -name '*.html' \) 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "${N:-0}" -gt 0 ]]; then
    INPUT="data/pilot/raw"
  fi
fi
echo "INFO: ingest from $INPUT"

echo "→ ingest"
"$PY" -m agentic_graphrag ingest --input "$INPUT"

if [[ "$SKIP_EXTRACT" -eq 0 ]]; then
  echo "→ build-graph (LLM extract)"
  # Prefer Neo4j; if down, build-graph without --no-llm fails — use memory only if forced
  set +e
  "$PY" -m agentic_graphrag build-graph
  BG_RC=$?
  set -e
  if [[ $BG_RC -ne 0 ]]; then
    echo "WARN: Neo4j build-graph failed (rc=$BG_RC). Extract may have partially written triples."
    echo "      Start Neo4j or re-run after: docker compose up -d neo4j"
    if [[ ! -f data/processed/triples.jsonl ]]; then
      echo "FAIL: no data/processed/triples.jsonl"
      exit 1
    fi
  fi

  TRIPLES="data/processed/triples.jsonl"
  if [[ ! -f "$TRIPLES" ]]; then
    echo "FAIL: expected $TRIPLES from LLM extract"
    exit 1
  fi
  echo "→ spotcheck --mode llm (human labels pending)"
  "$PY" -m agentic_graphrag spotcheck \
    --triples "$TRIPLES" \
    --mode llm \
    --out reports/triple_spotcheck_llm.jsonl \
    --limit 50
  echo "ACTION: edit reports/triple_spotcheck_llm.jsonl human_label → correct|incorrect"
  echo "        then: python -m agentic_graphrag score-spotcheck --in reports/triple_spotcheck_llm.jsonl"
else
  echo "SKIP extract"
fi

if [[ "$SKIP_CASES" -eq 0 ]]; then
  echo "→ run-cases (live LLM)"
  # Prefer Neo4j if up; else fail with hint (live path requires graph for multi-hop)
  RUN_DIR="reports/llm_run"
  mkdir -p "$RUN_DIR"
  set +e
  "$PY" -m agentic_graphrag run-cases --out "$RUN_DIR"
  RC=$?
  set -e
  if [[ $RC -ne 0 ]]; then
    echo "WARN: live run-cases against Neo4j failed; trying --memory-graph with seed for partial signal"
    "$PY" -m agentic_graphrag run-cases --memory-graph --out "$RUN_DIR"
  fi
  if [[ -f "$RUN_DIR/poc_run.jsonl" ]]; then
    cp "$RUN_DIR/poc_run.jsonl" reports/poc_run_llm.jsonl
  fi
  if [[ -f "$RUN_DIR/poc_accuracy.json" ]]; then
    cp "$RUN_DIR/poc_accuracy.json" reports/poc_accuracy_llm.json
  fi
else
  echo "SKIP cases"
fi

# Memo skeleton
MEMO="reports/G1_live_llm_memo.md"
if [[ ! -f "$MEMO" ]]; then
  cat > "$MEMO" <<'EOF'
# G1 → G2 Live LLM re-run memo (C2)

**Date**:
**Model**: (from .env LLM_STRONG_MODEL / LIGHT)
**Corpus**: interim `data/raw` / pilot `data/pilot/raw`

## Extract spotcheck

| metric | value |
|--------|-------|
| sample | see `reports/triple_spotcheck_llm.summary.json` |
| correct_rate | (after human labels) |
| pass ≥70% | yes/no |

## 20 cases

| metric | value |
|--------|-------|
| accuracy | see `reports/poc_accuracy_llm.json` |
| offline delta | vs `reports/poc_accuracy.json` |
| avg latency_ms | |
| total tokens | |

## Failures / themes

1.
2.

## Conclusion

- [ ] C2a extract gate
- [ ] C2b cases report archived
- Notes:
EOF
  echo "Wrote memo skeleton $MEMO"
fi

# Status fragment
"$PY" - <<'PY'
import json
from pathlib import Path
from datetime import datetime, timezone

def load(p):
    path = Path(p)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))

spot = load("reports/triple_spotcheck_llm.summary.json") or {}
acc = load("reports/poc_accuracy_llm.json") or {}
pending = spot.get("pending_human")
rate = spot.get("correct_rate")
if "pass_g1_extract_gate" in spot:
    extract_pass = bool(spot["pass_g1_extract_gate"])
elif pending is not None and int(pending) > 0:
    extract_pass = False
elif rate is None:
    extract_pass = False
else:
    extract_pass = float(rate) >= 0.70

cases_ran = bool(acc.get("total"))
out = {
    "gate": "C2_live_llm",
    "ts": datetime.now(timezone.utc).isoformat(),
    "extract": {
        "summary": spot,
        "pass": extract_pass,
        "human_action_required": (pending is None) or int(pending or 0) > 0 or rate is None,
    },
    "cases": {
        "accuracy": acc,
        "ran": cases_ran,
        "pass": cases_ran,  # full pass still needs human review of quality
    },
    "pass": extract_pass and cases_ran,
    "artifacts": {
        "spotcheck": "reports/triple_spotcheck_llm.jsonl",
        "spotcheck_summary": "reports/triple_spotcheck_llm.summary.json",
        "run": "reports/poc_run_llm.jsonl",
        "accuracy": "reports/poc_accuracy_llm.json",
        "memo": "reports/G1_live_llm_memo.md",
    },
}
Path("reports/llm_live_rerun.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
if out["extract"]["human_action_required"]:
    print("NOTE: human labeling still required for extract gate", flush=True)
PY

echo "=== C2 automated portion done ==="
echo "Next: human spotcheck labels → score-spotcheck → fill G1_live_llm_memo.md"
