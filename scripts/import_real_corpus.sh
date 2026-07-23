#!/usr/bin/env bash
# Import authorized real-domain documents into data/pilot/raw (or MANIFEST path).
# Does NOT invent product signoff — only copies + refreshes doc_count.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SRC="${1:-}"
if [[ -z "$SRC" || ! -d "$SRC" ]]; then
  echo "Usage: $0 /path/to/authorized_docs_dir"
  echo "Copies .md/.txt/.html into data/pilot/raw and prints MANIFEST checklist."
  exit 2
fi

DEST="${ROOT}/data/pilot/raw"
mkdir -p "$DEST"
n=0
while IFS= read -r -d '' f; do
  base="$(basename "$f")"
  # Prefix real_ to avoid clobbering synthetic pilot files if both coexist
  cp -n "$f" "$DEST/real_${base}" 2>/dev/null || cp "$f" "$DEST/real_${base}"
  n=$((n + 1))
done < <(find "$SRC" -type f \( -name '*.md' -o -name '*.txt' -o -name '*.html' \) -print0)

echo "Copied/refreshed ~$n docs into $DEST (real_* prefix)"
count="$(find "$DEST" -type f \( -name '*.md' -o -name '*.txt' -o -name '*.html' \) | wc -l | tr -d ' ')"
echo "Total docs in pilot/raw: $count"

MAN="$ROOT/data/pilot/MANIFEST.yaml"
if [[ -f "$MAN" ]]; then
  echo "Update MANIFEST.yaml: corpus.doc_count=$count, authorization.*, checklist.product_signoff"
  echo "Template: data/pilot/MANIFEST.template.yaml · Playbook: docs/REAL_DOMAIN_PLAYBOOK.md"
else
  cp "$ROOT/data/pilot/MANIFEST.template.yaml" "$MAN"
  echo "Created $MAN from template — fill domain/authorization and set product_signoff"
fi

echo "Next: edit MANIFEST → ./scripts/validate_pilot_corpus.sh → ingest + live extract"
