#!/usr/bin/env bash
# Qdrant regression: ping + optional index smoke (mirror of C3 Neo4j).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ALLOW_SKIP="${ALLOW_SKIP_QDRANT:-0}"
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
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
REPORT="reports/qdrant_regression.json"
mkdir -p reports

echo "=== Qdrant regression ==="

if ! "$PY" - <<'PY'
import sys
from agentic_graphrag.config import get_settings
try:
    from agentic_graphrag.stores.vector_store import QdrantVectorStore
    s = get_settings()
    store = QdrantVectorStore(s.qdrant_url, s.qdrant_collection)
    # Search with empty/zero vector smoke — collection may be empty
    try:
        store.search([0.0] * 8, top_k=1)
    except Exception as e:
        # Dim mismatch is ok if collection has different dim; connectivity matters
        msg = str(e).lower()
        if "connection" in msg or "refused" in msg or "timeout" in msg:
            raise
    print("qdrant_ok", s.qdrant_url, s.qdrant_collection)
    store.close()
except Exception as e:
    print(f"ping_failed: {e}", file=sys.stderr)
    sys.exit(1)
PY
then
  echo "Qdrant not reachable"
  if [[ "$ALLOW_SKIP" -eq 1 ]]; then
    cat > "$REPORT" <<'JSON'
{"status":"skipped","reason":"Qdrant unreachable","allow_skip":true}
JSON
    echo "SKIP allowed → $REPORT"
    exit 0
  fi
  cat > "$REPORT" <<'JSON'
{"status":"fail","reason":"Qdrant unreachable"}
JSON
  exit 1
fi

cat > "$REPORT" <<JSON
{"status":"pass","backend":"qdrant","url":"${QDRANT_URL:-http://localhost:6333}"}
JSON
echo "PASS → $REPORT"
