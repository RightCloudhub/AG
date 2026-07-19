#!/usr/bin/env bash
# C3 — Neo4j regression: build-graph (seed) + run-cases --neo4j --no-llm
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ALLOW_SKIP="${ALLOW_SKIP_NEO4J:-0}"
for arg in "$@"; do
  case "$arg" in
    --allow-skip) ALLOW_SKIP=1 ;;
    -h|--help)
      echo "Usage: $0 [--allow-skip]"
      exit 0
      ;;
  esac
done

PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
REPORT="reports/neo4j_regression.json"
CASES_OUT="reports"
mkdir -p reports

echo "=== C3 Neo4j regression ==="

docker_ok=0
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    docker_ok=1
  fi
fi

# WSL: Docker Desktop CLI may be under Windows path without daemon in WSL
if [[ $docker_ok -eq 0 ]] && command -v docker.exe >/dev/null 2>&1; then
  if docker.exe info >/dev/null 2>&1; then
    echo "WARN: docker.exe present but WSL integration may be incomplete"
  fi
fi

neo4j_ping() {
  "$PY" - <<'PY'
import sys
from agentic_graphrag.config import get_settings
try:
    from agentic_graphrag.stores.neo4j_store import Neo4jGraphStore
    s = get_settings()
    store = Neo4jGraphStore(s.neo4j_uri, s.neo4j_user, s.neo4j_password)
    store.ping()
    print(store.counts())
    store.close()
    sys.exit(0)
except Exception as e:
    print(f"ping_failed: {e}", file=sys.stderr)
    sys.exit(1)
PY
}

if ! neo4j_ping; then
  echo "Neo4j not reachable at NEO4J_URI (see .env / .env.example)"
  if [[ $docker_ok -eq 1 ]]; then
    echo "Attempting: docker compose up -d neo4j"
    docker compose up -d neo4j || true
    for i in $(seq 1 30); do
      if neo4j_ping; then
        echo "Neo4j became ready (attempt $i)"
        break
      fi
      sleep 2
    done
  fi
fi

if ! neo4j_ping >/tmp/agr_neo4j_counts.txt 2>/tmp/agr_neo4j_err.txt; then
  echo "FAIL/SKIP: Neo4j unavailable"
  cat /tmp/agr_neo4j_err.txt || true
  cat > "$REPORT" <<EOF
{
  "gate": "C3_neo4j_regression",
  "status": "skip",
  "pass": false,
  "reason": "neo4j_unavailable",
  "hint": "Start Docker Desktop + WSL integration, then: docker compose up -d neo4j"
}
EOF
  if [[ "$ALLOW_SKIP" == "1" ]]; then
    echo "=== C3 SKIP (--allow-skip) ==="
    exit 0
  fi
  echo "=== C3 FAIL (use --allow-skip to soft-skip) ==="
  exit 1
fi

echo "OK: Neo4j reachable $(cat /tmp/agr_neo4j_counts.txt)"

echo "→ build-graph seed → Neo4j"
# Must NOT use --memory-graph; --no-llm allows fallback — check backend in output
BUILD_OUT="$(mktemp)"
set +e
"$PY" -m agentic_graphrag build-graph \
  --triples data/processed/seed_triples.jsonl \
  --no-llm 2>"$BUILD_OUT.err" | tee "$BUILD_OUT"
BUILD_RC=${PIPESTATUS[0]}
set -e
if [[ $BUILD_RC -ne 0 ]]; then
  echo "FAIL: build-graph rc=$BUILD_RC"
  cat "$BUILD_OUT.err" || true
  exit 1
fi
if grep -q '"backend": "memory"' "$BUILD_OUT"; then
  echo "FAIL: build-graph fell back to memory (Neo4j write required for C3)"
  cat > "$REPORT" <<EOF
{
  "gate": "C3_neo4j_regression",
  "status": "fail",
  "pass": false,
  "reason": "build_graph_memory_fallback"
}
EOF
  exit 1
fi
if ! grep -q '"backend": "neo4j"' "$BUILD_OUT"; then
  echo "WARN: backend field not found; continuing if counts ok"
fi

echo "→ run-cases --no-llm --neo4j"
# Write dedicated report paths by temporarily using score after with custom out dir
RUN_DIR="reports/neo4j_run"
mkdir -p "$RUN_DIR"
set +e
"$PY" -m agentic_graphrag run-cases --no-llm --neo4j --out "$RUN_DIR"
RUN_RC=$?
set -e

# Normalize artifact names for gate
if [[ -f "$RUN_DIR/poc_run.jsonl" ]]; then
  cp "$RUN_DIR/poc_run.jsonl" reports/poc_run_neo4j.jsonl
fi
if [[ -f "$RUN_DIR/poc_accuracy.json" ]]; then
  cp "$RUN_DIR/poc_accuracy.json" reports/poc_accuracy_neo4j.json
fi

ACC=null
CORRECT=0
TOTAL=0
if [[ -f reports/poc_accuracy_neo4j.json ]]; then
  ACC="$("$PY" -c "import json; d=json.load(open('reports/poc_accuracy_neo4j.json')); print(d.get('accuracy', d.get('correct_rate', '')))")"
  CORRECT="$("$PY" -c "import json; d=json.load(open('reports/poc_accuracy_neo4j.json')); print(d.get('correct', 0))")"
  TOTAL="$("$PY" -c "import json; d=json.load(open('reports/poc_accuracy_neo4j.json')); print(d.get('total', 0))")"
fi

PASS=false
STATUS="fail"
if [[ $RUN_RC -eq 0 && "$TOTAL" -eq 20 && "$CORRECT" -eq 20 ]]; then
  PASS=true
  STATUS="pass"
elif [[ $RUN_RC -eq 0 && "$TOTAL" -gt 0 && -f reports/poc_accuracy_neo4j.json ]]; then
  # pass gate if accuracy ≥60% and ≥10 cases completed (aligned with G1 floor)
  if "$PY" - <<'PY'
import json, sys
d = json.load(open("reports/poc_accuracy_neo4j.json"))
acc = float(d.get("accuracy") or 0)
sys.exit(0 if acc >= 0.6 and int(d.get("total") or 0) >= 10 else 1)
PY
  then
    PASS=true
    STATUS="pass_partial"
  fi
fi

COUNTS="$(cat /tmp/agr_neo4j_counts.txt | tr -d '\n' | sed 's/"/\\"/g')"
cat > "$REPORT" <<EOF
{
  "gate": "C3_neo4j_regression",
  "status": "$STATUS",
  "pass": $PASS,
  "build_backend": "neo4j",
  "run_rc": $RUN_RC,
  "correct": $CORRECT,
  "total": $TOTAL,
  "accuracy": $ACC,
  "artifacts": {
    "run": "reports/poc_run_neo4j.jsonl",
    "accuracy": "reports/poc_accuracy_neo4j.json"
  },
  "neo4j_counts_after_ping": "$COUNTS"
}
EOF

echo "Wrote $REPORT"
if [[ "$PASS" == "true" ]]; then
  echo "=== C3 PASS ($STATUS) accuracy=$ACC ($CORRECT/$TOTAL) ==="
  exit 0
fi
echo "=== C3 FAIL ($STATUS) accuracy=$ACC ($CORRECT/$TOTAL) rc=$RUN_RC ==="
exit 1
