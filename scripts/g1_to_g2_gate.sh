#!/usr/bin/env bash
# Master runner for G1 → G2 transition conditions (C1/C2/C3)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

WITH_LLM=0
ALLOW_SKIP_NEO4J=0
for arg in "$@"; do
  case "$arg" in
    --with-llm) WITH_LLM=1 ;;
    --allow-skip-neo4j) ALLOW_SKIP_NEO4J=1 ;;
    -h|--help)
      cat <<EOF
Usage: $0 [--with-llm] [--allow-skip-neo4j]

  Default: validate C1, run C3 Neo4j regression, aggregate C2 from existing reports.
  --with-llm: also run scripts/llm_live_rerun.sh (needs LLM_API_KEY)
  --allow-skip-neo4j: C3 SKIP when Docker/Neo4j down (does not close the gate)
EOF
      exit 0
      ;;
  esac
done

PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
mkdir -p reports
chmod +x scripts/*.sh 2>/dev/null || true

echo "######## G1 → G2 transition gate ########"

C1_PASS=false
C2_PASS=false
C3_PASS=false
C1_STATUS="not_run"
C2_STATUS="not_run"
C3_STATUS="not_run"

# --- C1 ---
set +e
./scripts/validate_pilot_corpus.sh
C1_RC=$?
set -e
if [[ $C1_RC -eq 0 ]]; then
  C1_PASS=true
  C1_STATUS="pass"
else
  C1_STATUS="fail"
fi

# --- C2 ---
if [[ $WITH_LLM -eq 1 ]]; then
  set +e
  ./scripts/llm_live_rerun.sh
  C2_RC=$?
  set -e
  if [[ $C2_RC -ne 0 ]]; then
    C2_STATUS="fail_auto"
  fi
fi

if [[ -f reports/llm_live_rerun.json ]]; then
  eval "$("$PY" - <<'PY'
import json
from pathlib import Path
d=json.loads(Path("reports/llm_live_rerun.json").read_text())
print(f"C2_PASS={'true' if d.get('pass') else 'false'}")
ext=d.get("extract",{})
print(f"C2_STATUS={'pass' if d.get('pass') else ('pending_human' if ext.get('human_action_required') else 'fail')}")
PY
)"
elif [[ -f reports/poc_accuracy_llm.json ]]; then
  C2_STATUS="partial_artifacts"
  C2_PASS=false
else
  C2_STATUS="pending"
  C2_PASS=false
fi

# --- C3 ---
NEO_ARGS=()
if [[ $ALLOW_SKIP_NEO4J -eq 1 ]]; then
  NEO_ARGS+=(--allow-skip)
fi
set +e
./scripts/neo4j_regression.sh "${NEO_ARGS[@]+"${NEO_ARGS[@]}"}"
C3_RC=$?
set -e
if [[ -f reports/neo4j_regression.json ]]; then
  eval "$("$PY" - <<'PY'
import json
from pathlib import Path
d=json.loads(Path("reports/neo4j_regression.json").read_text())
st=d.get("status") or ("pass" if d.get("pass") else "fail")
print(f"C3_PASS={'true' if d.get('pass') else 'false'}")
print(f"C3_STATUS={st}")
PY
)"
else
  C3_STATUS="fail"
  C3_PASS=false
fi

# Aggregate
ALL_PASS=false
if [[ "$C1_PASS" == "true" && "$C2_PASS" == "true" && "$C3_PASS" == "true" ]]; then
  ALL_PASS=true
fi

export G2G_C1_PASS="$C1_PASS" G2G_C1_STATUS="$C1_STATUS"
export G2G_C2_PASS="$C2_PASS" G2G_C2_STATUS="$C2_STATUS"
export G2G_C3_PASS="$C3_PASS" G2G_C3_STATUS="$C3_STATUS"
export G2G_ALL_PASS="$ALL_PASS"

"$PY" - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

def b(name: str) -> bool:
    return os.environ.get(name, "false").lower() == "true"

status = {
    "gate": "G1_to_G2",
    "ts": datetime.now(timezone.utc).isoformat(),
    "pass": b("G2G_ALL_PASS"),
    "conditions": {
        "C1_pilot_corpus_P1_GOV_01": {
            "pass": b("G2G_C1_PASS"),
            "status": os.environ.get("G2G_C1_STATUS", "unknown"),
            "report": "reports/pilot_corpus_validation.json",
        },
        "C2_live_llm": {
            "pass": b("G2G_C2_PASS"),
            "status": os.environ.get("G2G_C2_STATUS", "unknown"),
            "report": "reports/llm_live_rerun.json",
        },
        "C3_neo4j_regression": {
            "pass": b("G2G_C3_PASS"),
            "status": os.environ.get("G2G_C3_STATUS", "unknown"),
            "report": "reports/neo4j_regression.json",
        },
    },
    "playbook": "plan/phases/g1-to-g2-transition.md",
    "next_actions": [],
}
c = status["conditions"]
if not c["C1_pilot_corpus_P1_GOV_01"]["pass"]:
    status["next_actions"].append(
        "Fill data/pilot/MANIFEST.yaml + ≥100 docs; run validate_pilot_corpus.sh"
    )
if c["C2_live_llm"]["status"] in (
    "pending",
    "pending_human",
    "partial_artifacts",
    "not_run",
    "fail_auto",
):
    status["next_actions"].append(
        "Run ./scripts/llm_live_rerun.sh; human-label spotcheck; score-spotcheck"
    )
if not c["C3_neo4j_regression"]["pass"]:
    status["next_actions"].append("Start Docker/Neo4j; ./scripts/neo4j_regression.sh")
Path("reports/G1_to_G2_status.json").write_text(
    json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(status, ensure_ascii=False, indent=2))
PY

echo ""
echo "======== Summary ========"
echo "C1 pilot corpus : $C1_STATUS (pass=$C1_PASS)"
echo "C2 live LLM     : $C2_STATUS (pass=$C2_PASS)"
echo "C3 Neo4j        : $C3_STATUS (pass=$C3_PASS)"
echo "Overall         : pass=$ALL_PASS"
echo "Status file     : reports/G1_to_G2_status.json"
echo "Playbook        : plan/phases/g1-to-g2-transition.md"

if [[ "$ALL_PASS" == "true" ]]; then
  exit 0
fi
exit 1
