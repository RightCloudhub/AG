#!/usr/bin/env bash
# C1 — P1-GOV-01 pilot corpus validation
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MANIFEST="${PILOT_MANIFEST:-data/pilot/MANIFEST.yaml}"
TEMPLATE="data/pilot/MANIFEST.template.yaml"
MIN_DOCS="${PILOT_MIN_DOCS:-100}"
STATUS=0

echo "=== C1 Pilot corpus validation (P1-GOV-01) ==="

if [[ ! -f "$MANIFEST" ]]; then
  echo "FAIL: $MANIFEST missing"
  echo "  → cp $TEMPLATE data/pilot/MANIFEST.yaml  and fill in"
  STATUS=1
  RAW_PATH="data/pilot/raw"
else
  echo "OK: manifest present → $MANIFEST"
  # naive YAML peek (no yq required)
  RAW_PATH="$(grep -E '^\s*raw_path:' "$MANIFEST" | head -1 | sed -E 's/.*raw_path:[[:space:]]*"?([^"#]+)"?.*/\1/' | tr -d '[:space:]')"
  RAW_PATH="${RAW_PATH:-data/pilot/raw}"
  DOMAIN_ID="$(grep -E '^\s*id:' "$MANIFEST" | head -1 | sed -E 's/.*id:[[:space:]]*"?([^"#]*)"?.*/\1/' | tr -d '[:space:]')"
  if [[ -z "$DOMAIN_ID" ]]; then
    echo "FAIL: domain.id empty in manifest"
    STATUS=1
  else
    echo "OK: domain.id=$DOMAIN_ID"
  fi
  if grep -qE 'authorization:|approved_by:' "$MANIFEST"; then
    APPROVED="$(grep -E '^\s*approved_by:' "$MANIFEST" | head -1 | sed -E 's/.*approved_by:[[:space:]]*"?([^"#]*)"?.*/\1/' | tr -d '[:space:]')"
    if [[ -z "$APPROVED" ]]; then
      echo "WARN: authorization.approved_by empty (product signoff pending)"
      STATUS=1
    else
      echo "OK: approved_by=$APPROVED"
    fi
  fi
fi

if [[ ! -d "$RAW_PATH" ]]; then
  echo "FAIL: raw_path not a directory: $RAW_PATH"
  STATUS=1
  DOC_COUNT=0
else
  # count md/txt/html files (exclude .gitkeep)
  DOC_COUNT="$(find "$RAW_PATH" -type f \( -name '*.md' -o -name '*.txt' -o -name '*.html' -o -name '*.htm' \) 2>/dev/null | wc -l | tr -d ' ')"
  echo "INFO: doc_count=$DOC_COUNT (min=$MIN_DOCS) under $RAW_PATH"
  if [[ "$DOC_COUNT" -lt "$MIN_DOCS" ]]; then
    echo "FAIL: need ≥$MIN_DOCS documents, found $DOC_COUNT"
    # Interim note: still useful for smoke when PILOT_ALLOW_INTERIM=1
    if [[ "${PILOT_ALLOW_INTERIM:-0}" == "1" ]]; then
      echo "WARN: PILOT_ALLOW_INTERIM=1 — treating as soft-fail for engineering smoke only"
    else
      STATUS=1
    fi
  else
    echo "OK: doc count ≥ $MIN_DOCS"
  fi
fi

# Optional ingest smoke when docs exist
if [[ "$DOC_COUNT" -gt 0 ]]; then
  if [[ -x .venv/bin/python ]] || command -v python3 >/dev/null; then
    PY="${ROOT}/.venv/bin/python"
    [[ -x "$PY" ]] || PY="python3"
    OUT_DIR="$(mktemp -d)"
    set +e
    "$PY" -c "
from agentic_graphrag.cli import ingest_main
ingest_main(['--input', '${RAW_PATH}', '--out', '${OUT_DIR}/chunks.jsonl'])
" 2>"$OUT_DIR/err.txt"
    RC=$?
    set -e
    if [[ $RC -eq 0 && -f "$OUT_DIR/chunks.jsonl" ]]; then
      CHUNKS="$(wc -l < "$OUT_DIR/chunks.jsonl" | tr -d ' ')"
      echo "OK: ingest smoke → $CHUNKS chunks"
    else
      echo "FAIL: ingest smoke failed (rc=$RC)"
      sed -n '1,20p' "$OUT_DIR/err.txt" || true
      STATUS=1
    fi
    rm -rf "$OUT_DIR"
  fi
else
  echo "SKIP: ingest smoke (no documents)"
fi

RESULT_JSON="reports/pilot_corpus_validation.json"
mkdir -p reports
cat > "$RESULT_JSON" <<EOF
{
  "gate": "C1_P1_GOV_01",
  "pass": $([[ $STATUS -eq 0 ]] && echo true || echo false),
  "manifest": "$MANIFEST",
  "raw_path": "$RAW_PATH",
  "doc_count": $DOC_COUNT,
  "min_docs": $MIN_DOCS,
  "interim_corpus_note": "POC interim lives in data/raw/ (6 docs); does not close C1"
}
EOF
echo "Wrote $RESULT_JSON"

if [[ $STATUS -eq 0 ]]; then
  echo "=== C1 PASS ==="
else
  echo "=== C1 FAIL (see above; template: $TEMPLATE) ==="
fi
exit "$STATUS"
