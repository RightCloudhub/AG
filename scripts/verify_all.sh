#!/usr/bin/env bash
# Master verification pack for open gates (engineering + formal AC/G2/G3/G4).
#
# Default: offline runnable suite (does NOT claim formal product acceptance).
# Live / HTTP / formal product items stay open until preconditions exist.
#
# Usage (repo root):
#   ./scripts/verify_all.sh
#   ./scripts/verify_all.sh --quick
#   ./scripts/verify_all.sh --with-tests
#   ./scripts/verify_all.sh --with-llm
#   ./scripts/verify_all.sh --target http://127.0.0.1:8000
#   ./scripts/verify_all.sh --reuse --skip-heavy
#   ./scripts/verify_all.sh --require-formal   # exit 1 if formal gates still open
#
# Writes:
#   reports/verify_all/VERIFY_ALL_status.json
#   reports/verify_all/VERIFY_ALL_status.md
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ROOT}/.env"
  set +a
fi

PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

QUICK=0
WITH_TESTS=0
WITH_LLM=0
WITH_NEO4J=0
ALLOW_SKIP_NEO4J=1
REUSE=0
SKIP_HEAVY=0
SKIP_LINT=0
REQUIRE_FORMAL=0
HTTP_TARGET=""
LOAD_N=20

for arg in "$@"; do
  case "$arg" in
    --quick) QUICK=1; SKIP_HEAVY=1 ;;
    --with-tests) WITH_TESTS=1 ;;
    --with-llm) WITH_LLM=1 ;;
    --with-neo4j) WITH_NEO4J=1; ALLOW_SKIP_NEO4J=0 ;;
    --allow-skip-neo4j) ALLOW_SKIP_NEO4J=1 ;;
    --reuse) REUSE=1 ;;
    --skip-heavy) SKIP_HEAVY=1 ;;
    --skip-lint) SKIP_LINT=1 ;;
    --require-formal) REQUIRE_FORMAL=1 ;;
    --target=*) HTTP_TARGET="${arg#--target=}" ;;
    --n=*) LOAD_N="${arg#--n=}" ;;
    -h|--help)
      sed -n '2,25p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    --target|--n)
      echo "Use --target=URL and --n=N (equals form)" >&2
      exit 2
      ;;
    *)
      echo "Unknown arg: $arg (see --help)" >&2
      exit 2
      ;;
  esac
done

OUT_DIR="reports/verify_all"
mkdir -p "$OUT_DIR" reports reports/g3_offline reports/g3_live
chmod +x scripts/*.sh 2>/dev/null || true

# Exported for embedded Python aggregators
export VERIFY_HTTP_TARGET="$HTTP_TARGET"
export VERIFY_WITH_LLM="$WITH_LLM"
export VERIFY_QUICK="$QUICK"
export VERIFY_REQUIRE_FORMAL="$REQUIRE_FORMAL"
export VERIFY_OUT_DIR="$OUT_DIR"

RESULTS_TSV="${OUT_DIR}/_results.tsv"
export VERIFY_TSV="$RESULTS_TSV"
: >"$RESULTS_TSV"

log() { printf '%s\n' "$*"; }
section() { log ""; log "######## $* ########"; }

# record id|title|status|pass|artifact|note
# status: pass|fail|skip|blocked|partial|info
record() {
  local id="$1" title="$2" status="$3" pass="$4" artifact="${5:-}" note="${6:-}"
  # TSV-safe: strip pipes/newlines from free text
  note="${note//$'\n'/ }"
  note="${note//|/\/}"
  title="${title//|/\/}"
  artifact="${artifact//|/\/}"
  printf '%s|%s|%s|%s|%s|%s\n' "$id" "$title" "$status" "$pass" "$artifact" "$note" >>"$RESULTS_TSV"
  log "  [$status] $id — $title (pass=$pass)${note:+ · $note}"
}

run_ok() {
  # run_ok id title artifact -- cmd...
  local id="$1" title="$2" artifact="$3"
  shift 3
  set +e
  "$@"
  local rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then
    record "$id" "$title" "pass" "true" "$artifact" "rc=0"
    return 0
  fi
  record "$id" "$title" "fail" "false" "$artifact" "rc=$rc"
  return 0
}

has_llm_key() {
  local k="${LLM_API_KEY:-}"
  [[ -n "$k" ]] || return 1
  [[ "$k" != "sk-your-key" ]] || return 1
  [[ "$k" != *"your-key"* ]] || return 1
  return 0
}

# ---------------------------------------------------------------------------
section "E1 code metrics"
run_ok E1 "code_metrics" "scripts/check_code_metrics.py" \
  "$PY" scripts/check_code_metrics.py

# ---------------------------------------------------------------------------
if [[ $SKIP_LINT -eq 0 ]]; then
  section "E2 ruff lint + format"
  if command -v ruff >/dev/null 2>&1 || [[ -x "${ROOT}/.venv/bin/ruff" ]]; then
    RUFF="${ROOT}/.venv/bin/ruff"
    [[ -x "$RUFF" ]] || RUFF="ruff"
    set +e
    "$RUFF" check src tests scripts
    RC1=$?
    "$RUFF" format --check src tests scripts
    RC2=$?
    set -e
    if [[ $RC1 -eq 0 && $RC2 -eq 0 ]]; then
      record E2 "ruff_lint_format" "pass" "true" "" "check+format"
    else
      record E2 "ruff_lint_format" "fail" "false" "" "check_rc=$RC1 format_rc=$RC2"
    fi
  else
    record E2 "ruff_lint_format" "skip" "false" "" "ruff not installed"
  fi
else
  record E2 "ruff_lint_format" "skip" "true" "" "--skip-lint"
fi

# ---------------------------------------------------------------------------
if [[ $WITH_TESTS -eq 1 ]]; then
  section "E3 unit tests + coverage≥80"
  run_ok E3 "unit_tests_cov80" "pytest" \
    "$PY" -m pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q
else
  record E3 "unit_tests_cov80" "skip" "true" "" "pass --with-tests to run"
fi

# ---------------------------------------------------------------------------
section "C1 pilot corpus (engineering)"
run_ok C1 "pilot_corpus_validate" "reports/pilot_corpus_validation.json" \
  ./scripts/validate_pilot_corpus.sh

# Product real-domain (cannot be closed by engineering alone)
section "C1p real-domain product signoff"
C1P_LINE="$("$PY" - <<'PY'
from pathlib import Path
try:
    import yaml
except ImportError:
    print("blocked|false|yaml missing")
    raise SystemExit
p = Path("data/pilot/MANIFEST.yaml")
if not p.exists():
    print("blocked|false|missing MANIFEST.yaml")
    raise SystemExit
m = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
auth = m.get("authorization") or {}
src = str(auth.get("source") or "")
cl = m.get("checklist") or {}
synth = "synthetic" in src.lower()
ok = bool(cl.get("product_signoff")) and not synth and bool(src)
note = f"source={src}"
if not ok:
    note += "; need authorized real domain (docs/REAL_DOMAIN_PLAYBOOK.md)"
print(f"{'pass' if ok else 'blocked'}|{str(ok).lower()}|{note}")
PY
)"
IFS='|' read -r C1P_ST C1P_PASS C1P_NOTE <<<"$C1P_LINE"
record C1p "real_domain_product" "$C1P_ST" "$C1P_PASS" "data/pilot/MANIFEST.yaml" "$C1P_NOTE"

# ---------------------------------------------------------------------------
section "C2 live LLM (artifact or --with-llm)"
if [[ $WITH_LLM -eq 1 ]]; then
  if has_llm_key; then
    run_ok C2 "live_llm_rerun" "reports/llm_live_rerun.json" ./scripts/llm_live_rerun.sh
  else
    record C2 "live_llm_rerun" "blocked" "false" "reports/g2_heldout_live_status.json" "LLM_API_KEY missing"
  fi
elif [[ -f reports/llm_live_rerun.json ]]; then
  C2_LINE="$("$PY" - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("reports/llm_live_rerun.json").read_text(encoding="utf-8"))
ok = bool(d.get("pass"))
print(f"{'pass' if ok else 'partial'}|{str(ok).lower()}|from artifact")
PY
)"
  IFS='|' read -r C2_ST C2_PASS C2_NOTE <<<"$C2_LINE"
  record C2 "live_llm_rerun" "$C2_ST" "$C2_PASS" "reports/llm_live_rerun.json" "$C2_NOTE"
else
  record C2 "live_llm_rerun" "skip" "false" "" "no artifact; pass --with-llm"
fi

# ---------------------------------------------------------------------------
section "C3 Neo4j regression"
if [[ $WITH_NEO4J -eq 1 || $QUICK -eq 0 ]]; then
  NEO_ARGS=()
  if [[ $ALLOW_SKIP_NEO4J -eq 1 ]]; then
    NEO_ARGS+=(--allow-skip)
  fi
  set +e
  ./scripts/neo4j_regression.sh "${NEO_ARGS[@]+"${NEO_ARGS[@]}"}"
  C3_RC=$?
  set -e
  if [[ -f reports/neo4j_regression.json ]]; then
    C3_LINE="$("$PY" - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("reports/neo4j_regression.json").read_text(encoding="utf-8"))
st = d.get("status") or ("pass" if d.get("pass") else "fail")
ok = bool(d.get("pass"))
# allow-skip / unavailable: not an engineering failure when Neo4j is opt-in
if st in {"skip", "pass_partial"}:
    ok = True if st == "pass_partial" or d.get("reason") == "neo4j_unavailable" else ok
    if st == "skip" and d.get("reason") == "neo4j_unavailable":
        st = "skip"
        ok = True
print(f"{st}|{str(ok).lower()}|{d.get('reason') or d.get('status') or 'ok'}")
PY
)"
    IFS='|' read -r C3_ST C3_PASS C3_NOTE <<<"$C3_LINE"
    record C3 "neo4j_regression" "$C3_ST" "$C3_PASS" "reports/neo4j_regression.json" "script_rc=$C3_RC note=$C3_NOTE"
  else
    record C3 "neo4j_regression" "fail" "false" "" "rc=$C3_RC no report"
  fi
else
  record C3 "neo4j_regression" "skip" "true" "" "--quick"
fi

# ---------------------------------------------------------------------------
section "G2 heldout offline formal pack"
if [[ $SKIP_HEAVY -eq 1 ]]; then
  if [[ -f reports/g2_heldout/g2_heldout_eval.json ]]; then
    record G2o "g2_heldout_offline" "skip" "true" "reports/g2_heldout/g2_heldout_eval.json" "reuse artifact (--skip-heavy)"
  else
    record G2o "g2_heldout_offline" "skip" "false" "" "no artifact and --skip-heavy"
  fi
elif [[ $REUSE -eq 1 && -f reports/g2_heldout/g2_heldout_eval.json ]]; then
  record G2o "g2_heldout_offline" "pass" "true" "reports/g2_heldout/g2_heldout_eval.json" "--reuse existing"
else
  run_ok G2o "g2_heldout_offline" "reports/g2_heldout/g2_heldout_eval.json" \
    ./scripts/g2_formal_eval.sh
fi

# Parse G2 offline trend flags into record G2t
if [[ -f reports/g2_heldout/g2_heldout_eval.json ]]; then
  G2T_LINE="$("$PY" - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("reports/g2_heldout/g2_heldout_eval.json").read_text(encoding="utf-8"))
ag = d.get("systems", {}).get("agentic", {})
delta = d.get("delta_agentic_minus_baseline") or {}
# support both nested and flat shapes
dpp = delta.get("accuracy_pp")
if dpp is None:
    dpp = delta.get("delta_accuracy_pp")
if dpp is None:
    dpp = d.get("delta_accuracy_pp")
if dpp is None:
    # fallback: accuracy pct fields
    ba = (d.get("systems") or {}).get("baseline", {}).get("accuracy_pct")
    aa = ag.get("accuracy_pct")
    if aa is not None and ba is not None:
        dpp = float(aa) - float(ba)
rec = ag.get("evidence_recall")
ok15 = dpp is not None and float(dpp) >= 15.0
ok75 = rec is not None and float(rec) >= 0.75
trend = ok15 and ok75
print(
    f"{'pass' if trend else 'partial'}|{str(trend).lower()}|"
    f"delta_pp={dpp} recall={rec} +15pp={ok15} recall>=0.75={ok75}"
)
PY
)"
  IFS='|' read -r G2T_ST G2T_PASS G2T_NOTE <<<"$G2T_LINE"
  record G2t "g2_trend_offline_heldout" "$G2T_ST" "$G2T_PASS" \
    "reports/g2_heldout/g2_heldout_eval.json" "$G2T_NOTE"
else
  record G2t "g2_trend_offline_heldout" "skip" "false" "" "no eval json"
fi

# ---------------------------------------------------------------------------
section "G2 heldout live formal"
if [[ $WITH_LLM -eq 1 ]]; then
  if has_llm_key; then
    run_ok G2l "g2_heldout_live" "reports/g2_heldout_live/g2_heldout_eval.json" \
      ./scripts/g2_formal_eval.sh --with-llm
  else
    record G2l "g2_heldout_live" "blocked" "false" "reports/g2_heldout_live_status.json" \
      "LLM_API_KEY missing"
  fi
elif [[ -f reports/g2_heldout_live/g2_heldout_eval.json ]] || \
     [[ -f reports/g2_heldout_live/g2_heldout_eval_rescored.json ]]; then
  record G2l "g2_heldout_live" "partial" "false" "reports/g2_heldout_live/" \
    "artifact exists; formal claim still needs real-domain + human gold"
else
  record G2l "g2_heldout_live" "skip" "false" "" "pass --with-llm"
fi

# Human gold signoff (process): queue file + optional signed GOLD_SIGNOFF.md
if [[ -f evals/datasets/review_queue_gold.jsonl ]]; then
  if grep -q 'Signed:\*\* _(pending)_' evals/datasets/GOLD_SIGNOFF.md 2>/dev/null \
    || ! grep -q 'Signed:' evals/datasets/GOLD_SIGNOFF.md 2>/dev/null; then
    record G2h "human_gold_signoff" "partial" "false" \
      "evals/datasets/review_queue_gold.jsonl" "queue ready; human sign-off still pending"
  elif grep -Eq 'Signed:\*\* [^_]' evals/datasets/GOLD_SIGNOFF.md 2>/dev/null; then
    record G2h "human_gold_signoff" "pass" "true" \
      "evals/datasets/GOLD_SIGNOFF.md" "sign-off recorded"
  else
    record G2h "human_gold_signoff" "partial" "false" \
      "evals/datasets/review_queue_gold.jsonl" "queue present; complete GOLD_SIGNOFF.md"
  fi
else
  record G2h "human_gold_signoff" "blocked" "false" \
    "evals/datasets/ANNOTATION_SPEC.md" "no review queue file; human sign-off open"
fi

# ---------------------------------------------------------------------------
section "G3 offline P95 smoke (not formal AC-4)"
run_ok G3p "p95_offline_inprocess" "reports/g3_offline/load_p95.json" \
  "$PY" scripts/p3_load_http.py --n "$LOAD_N" --out reports/g3_offline/load_p95.json

if [[ -n "$HTTP_TARGET" ]]; then
  section "G3 live HTTP P95 (formal AC-4 cold path)"
  run_ok G3l "p95_http_live" "reports/g3_live/load_p95.json" \
    "$PY" scripts/p3_load_http.py \
      --target "$HTTP_TARGET" \
      --n "$LOAD_N" \
      --concurrency 4 \
      --out reports/g3_live/load_p95.json
else
  record G3l "p95_http_live" "skip" "false" "reports/g3_live/load_p95.json" \
    "pass --target=http://host:8000 (AGR_ALLOW_LLM=1 agr-api)"
fi

# Formal AC-4: only HTTP live path can close; offline always formal=false.
# Prefer this-run live report when --target set; else read offline + note stale live.
AC4_LINE="$("$PY" - <<'PY'
import json
import os
from pathlib import Path

target = (os.environ.get("VERIFY_HTTP_TARGET") or "").strip()
live_p = Path("reports/g3_live/load_p95.json")
off_p = Path("reports/g3_offline/load_p95.json")

def load(p: Path):
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))

if target:
    d = load(live_p)
    if not d:
        print("fail|false|--target set but no reports/g3_live/load_p95.json")
    else:
        t = d.get("targets") or {}
        formal = bool(t.get("formal_ac4") or t.get("formal_ac4_cold"))
        print(
            f"{'pass' if formal else 'fail'}|{str(formal).lower()}|"
            f"mode={d.get('mode')} live_ok={d.get('live_ok_count', 0)} path={live_p}"
        )
else:
    d = load(off_p)
    if not d:
        print("skip|false|no offline load_p95.json")
    else:
        t = d.get("targets") or {}
        formal = bool(t.get("formal_ac4") or t.get("formal_ac4_cold"))
        note = (
            f"mode={d.get('mode')} live_ok={d.get('live_ok_count', 0)} "
            f"(offline cannot close AC-4; use --target=...)"
        )
        # Never report offline as formal pass
        print(f"{'pass' if formal else 'fail'}|false|{note}")
PY
)"
IFS='|' read -r AC4_ST AC4_PASS AC4_NOTE <<<"$AC4_LINE"
record AC4 "formal_ac4_cold" "$AC4_ST" "$AC4_PASS" "" "$AC4_NOTE"

# ---------------------------------------------------------------------------
section "G3 guardrails + load smoke"
run_ok G3g "guardrails_offline" "reports/p3_perf_guardrails.json" \
  "$PY" scripts/p3_guardrail_and_load.py --out reports/p3_perf_guardrails.json

# ---------------------------------------------------------------------------
section "G3 EV offline pack (heldout / triage / incremental)"
if [[ $SKIP_HEAVY -eq 1 ]]; then
  if [[ $REUSE -eq 1 ]]; then
    set +e
    "$PY" scripts/p3_ev_offline.py --skip-runs
    EV_RC=$?
    set -e
    if [[ $EV_RC -eq 0 ]]; then
      record G3e "p3_ev_offline" "pass" "true" "reports/g3_offline/G3_review_scaffold.json" "--skip-runs"
    else
      record G3e "p3_ev_offline" "partial" "false" "reports/g3_offline/" "skip-runs rc=$EV_RC"
    fi
  else
    record G3e "p3_ev_offline" "skip" "true" "reports/g3_offline/" "--skip-heavy (use --reuse --skip-heavy to assemble)"
  fi
elif [[ $REUSE -eq 1 ]]; then
  run_ok G3e "p3_ev_offline" "reports/g3_offline/G3_review_scaffold.json" \
    "$PY" scripts/p3_ev_offline.py --skip-runs
else
  run_ok G3e "p3_ev_offline" "reports/g3_offline/G3_review_scaffold.json" \
    "$PY" scripts/p3_ev_offline.py
fi

# AC-5 incremental smoke from p3 pack
if [[ -f reports/g3_offline/incremental_drill.json ]]; then
  AC5_LINE="$("$PY" - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("reports/g3_offline/incremental_drill.json").read_text(encoding="utf-8"))
ok = bool(d.get("post_query_ok") or d.get("batch_accepted"))
print(f"{'pass' if ok else 'partial'}|{str(ok).lower()}|offline smoke only; production drill open")
PY
)"
  IFS='|' read -r AC5_ST AC5_PASS AC5_NOTE <<<"$AC5_LINE"
  record AC5 "incremental_offline_smoke" "$AC5_ST" "$AC5_PASS" \
    "reports/g3_offline/incremental_drill.json" "$AC5_NOTE"
else
  record AC5 "incremental_offline_smoke" "skip" "false" "" "run p3_ev_offline first"
fi

# ---------------------------------------------------------------------------
section "G4 / process blockers (status only)"
# G4 process: engineering scaffolds exist; formal still open without deploy evidence
mkdir -p reports/g4_scaffold
if [[ ! -f reports/g4_scaffold/checklist.json ]]; then
  cat > reports/g4_scaffold/checklist.json <<'JSON'
{
  "gray_release_days": 0,
  "feedback_count": 0,
  "alerts_wired": false,
  "budget_fuse_verified": false,
  "audit_sample_verified": false,
  "ac_formal_all": false,
  "note": "Fill after staging/prod verification; engineering scaffolds only"
}
JSON
fi
G4_LINE="$("$PY" - <<'PY'
import json
from pathlib import Path
p = Path("reports/g4_scaffold/checklist.json")
d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
gray = int(d.get("gray_release_days") or 0) >= 14 and int(d.get("feedback_count") or 0) > 0
alerts = bool(d.get("alerts_wired") and d.get("budget_fuse_verified") and d.get("audit_sample_verified"))
ac = bool(d.get("ac_formal_all"))
print(f"{'pass' if gray else 'partial'}|{str(gray).lower()}|gray_days={d.get('gray_release_days')} feedback={d.get('feedback_count')}")
print(f"{'pass' if alerts else 'partial'}|{str(alerts).lower()}|ops checklist in reports/g4_scaffold/checklist.json")
print(f"{'pass' if ac else 'blocked'}|{str(ac).lower()}|set ac_formal_all after AC-1..7 evidence")
PY
)"
mapfile -t G4_LINES <<<"$G4_LINE"
IFS='|' read -r G4A_ST G4A_PASS G4A_NOTE <<<"${G4_LINES[0]}"
IFS='|' read -r G4B_ST G4B_PASS G4B_NOTE <<<"${G4_LINES[1]}"
IFS='|' read -r G4C_ST G4C_PASS G4C_NOTE <<<"${G4_LINES[2]}"
record G4a "gray_release_2w_feedback" "$G4A_ST" "$G4A_PASS" "reports/g4_scaffold/checklist.json" "$G4A_NOTE"
record G4b "prod_alerts_budget_audit" "$G4B_ST" "$G4B_PASS" "docs/ops-runbook.md" "$G4B_NOTE"
record G4c "prd_ac_all_closed" "$G4C_ST" "$G4C_PASS" "docs/IMPORTANT.md" "$G4C_NOTE"

# ---------------------------------------------------------------------------
section "Aggregate report"
"$PY" - <<'PY'
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

tsv = Path(os.environ["VERIFY_TSV"])
out_dir = Path(os.environ["VERIFY_OUT_DIR"])
rows = []
for line in tsv.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    parts = line.split("|", 5)
    while len(parts) < 6:
        parts.append("")
    rid, title, status, passed, artifact, note = parts
    rows.append(
        {
            "id": rid,
            "title": title,
            "status": status,
            "pass": passed.lower() == "true",
            "artifact": artifact,
            "note": note,
        }
    )

eng_ids = {"E1", "E2", "E3", "C1", "G3p", "G3g"}
# E2/E3 may be skip-with-pass-true
def eng_ok(r: dict) -> bool:
    if r["id"] not in eng_ids:
        return True
    if r["status"] == "skip" and r["pass"]:
        return True
    return r["pass"] and r["status"] in {"pass", "partial"}

# stricter: E1 and G3p/G3g must pass if not skip
must_eng = [r for r in rows if r["id"] in {"E1", "C1", "G3p", "G3g"}]
engineering_pass = all(
    (r["pass"] and r["status"] in {"pass", "partial"})
    or (r["status"] == "skip" and r["pass"])
    for r in must_eng
)
# lint fail fails engineering if run
for r in rows:
    if r["id"] == "E2" and r["status"] == "fail":
        engineering_pass = False
    if r["id"] == "E3" and r["status"] == "fail":
        engineering_pass = False

formal_ids = {"C1p", "G2l", "G2h", "AC4", "G4a", "G4b", "G4c"}
formal_rows = [r for r in rows if r["id"] in formal_ids]
formal_pass = all(r["pass"] and r["status"] == "pass" for r in formal_rows)

# G2 trend formal claim never true without real domain
g2_formal = False
for r in rows:
    if r["id"] == "G2l" and r["pass"] and r["status"] == "pass":
        g2_formal = True
c1p = next((r for r in rows if r["id"] == "C1p"), None)
if c1p and not c1p["pass"]:
    g2_formal = False

blockers = []
for r in rows:
    if r["status"] in {"blocked", "fail"} or (
        r["id"] in formal_ids and not r["pass"]
    ):
        blockers.append(
            {
                "id": r["id"],
                "title": r["title"],
                "status": r["status"],
                "note": r["note"],
            }
        )

next_actions = []
by_id = {r["id"]: r for r in rows}
if by_id.get("C1p") and not by_id["C1p"]["pass"]:
    next_actions.append("Product: real domain + MANIFEST authorization (docs/REAL_DOMAIN_PLAYBOOK.md)")
if by_id.get("G2h") and not by_id["G2h"]["pass"]:
    next_actions.append("Human gold sample sign-off (ANNOTATION_SPEC + review queue)")
if by_id.get("G2l") and by_id["G2l"]["status"] in {"skip", "blocked"}:
    next_actions.append("./scripts/verify_all.sh --with-llm  # or g2_formal_eval.sh --with-llm")
if by_id.get("AC4") and not by_id["AC4"]["pass"]:
    next_actions.append(
        "Staging AC-4: AGR_ALLOW_LLM=1 agr-api && "
        "./scripts/verify_all.sh --target=http://127.0.0.1:8000 --n=40"
    )
if by_id.get("G4a") and not by_id["G4a"]["pass"]:
    next_actions.append("G4: deploy gray release, collect ≥2w feedback, close AC-1..7")
if by_id.get("E1") and not by_id["E1"]["pass"]:
    next_actions.insert(0, "Fix code metrics: python scripts/check_code_metrics.py")

report = {
    "gate": "VERIFY_ALL",
    "ts": datetime.now(timezone.utc).isoformat(),
    "engineering_pass": engineering_pass,
    "formal_pass": formal_pass,
    "g2_formal_claim_ok": g2_formal,
    "flags": {
        "quick": os.environ.get("VERIFY_QUICK") == "1",
        "with_llm": os.environ.get("VERIFY_WITH_LLM") == "1",
        "http_target": os.environ.get("VERIFY_HTTP_TARGET") or None,
        "require_formal": os.environ.get("VERIFY_REQUIRE_FORMAL") == "1",
    },
    "checks": rows,
    "blockers": blockers,
    "next_actions": next_actions,
    "legend": {
        "engineering_pass": "Runnable offline suite green (metrics/corpus/P95 smoke/guardrails)",
        "formal_pass": "Product/live AC gates closed — expected false until real domain + live evidence",
        "note": "Offline P95 met ≠ formal AC-4 (needs live_ok cold path)",
    },
    "docs": {
        "important": "docs/IMPORTANT.md",
        "roadmap": "plan/roadmap.md",
        "real_domain": "docs/REAL_DOMAIN_PLAYBOOK.md",
        "p95_notes": "reports/g3_offline/P95_NOTES.md",
    },
}

out_json = out_dir / "VERIFY_ALL_status.json"
out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# Markdown
lines = [
    "# VERIFY_ALL status",
    "",
    f"- generated: `{report['ts']}`",
    f"- **engineering_pass**: `{engineering_pass}`",
    f"- **formal_pass**: `{formal_pass}`",
    f"- **g2_formal_claim_ok**: `{g2_formal}`",
    "",
    "## Checks",
    "",
    "| ID | Title | Status | Pass | Artifact | Note |",
    "|----|-------|--------|------|----------|------|",
]
for r in rows:
    lines.append(
        f"| {r['id']} | {r['title']} | {r['status']} | {r['pass']} | "
        f"`{r['artifact']}` | {r['note']} |"
    )
lines.extend(["", "## Blockers", ""])
if blockers:
    for b in blockers:
        lines.append(f"- **{b['id']}** ({b['status']}): {b['title']} — {b['note']}")
else:
    lines.append("- _(none)_")
lines.extend(["", "## Next actions", ""])
for a in next_actions:
    lines.append(f"1. {a}")
if not next_actions:
    lines.append("1. _(none)_")
lines.extend(
    [
        "",
        "## How to re-run",
        "",
        "```bash",
        "./scripts/verify_all.sh                  # offline full",
        "./scripts/verify_all.sh --quick          # metrics + smoke",
        "./scripts/verify_all.sh --with-tests     # + pytest cov",
        "./scripts/verify_all.sh --with-llm       # + live C2/G2",
        "./scripts/verify_all.sh --target=http://127.0.0.1:8000",
        "./scripts/verify_all.sh --require-formal # fail if product gates open",
        "```",
        "",
    ]
)
out_md = out_dir / "VERIFY_ALL_status.md"
out_md.write_text("\n".join(lines), encoding="utf-8")
print(json.dumps(
    {
        "engineering_pass": engineering_pass,
        "formal_pass": formal_pass,
        "blockers": len(blockers),
        "json": str(out_json),
        "md": str(out_md),
    },
    indent=2,
))
# exit code via file for bash
exit_code = 0
if not engineering_pass:
    exit_code = 1
if os.environ.get("VERIFY_REQUIRE_FORMAL") == "1" and not formal_pass:
    exit_code = 1
Path(out_dir / "_exit_code").write_text(str(exit_code), encoding="utf-8")
PY

echo ""
echo "======== VERIFY_ALL Summary ========"
if [[ -f "$OUT_DIR/VERIFY_ALL_status.md" ]]; then
  # show header + engineering/formal lines
  head -n 8 "$OUT_DIR/VERIFY_ALL_status.md"
fi
echo "Full report: $OUT_DIR/VERIFY_ALL_status.json"
echo "Markdown:    $OUT_DIR/VERIFY_ALL_status.md"

EXIT_CODE=0
if [[ -f "$OUT_DIR/_exit_code" ]]; then
  EXIT_CODE="$(cat "$OUT_DIR/_exit_code")"
fi
exit "$EXIT_CODE"
