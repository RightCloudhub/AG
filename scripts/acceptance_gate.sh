#!/usr/bin/env bash
# Unified acceptance gate orchestrator (G2/G3/G4 engineering evidence).
# Offline by default; optional live backends never fake PASS when skipped.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

WITH_LLM=0
WITH_NEO4J=0
WITH_QDRANT=0
WITH_LOAD=0
SKIP_EVAL=0
for arg in "$@"; do
  case "$arg" in
    --with-llm) WITH_LLM=1 ;;
    --with-neo4j) WITH_NEO4J=1 ;;
    --with-qdrant) WITH_QDRANT=1 ;;
    --with-load) WITH_LOAD=1 ;;
    --skip-eval) SKIP_EVAL=1 ;;
    -h|--help)
      cat <<EOF
Usage: $0 [--with-llm] [--with-neo4j] [--with-qdrant] [--with-load] [--skip-eval]
EOF
      exit 0
      ;;
  esac
done

PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p reports

STATUS="reports/ACCEPTANCE_STATUS.json"
blockers=()
notes=()

echo "=== Acceptance gate ==="

# Wave A: heldout offline formal pack (reuse p3 or g2 script)
if [[ "$SKIP_EVAL" -eq 0 ]]; then
  if [[ -x scripts/g2_formal_eval.sh ]]; then
    bash scripts/g2_formal_eval.sh || notes+=("g2_formal_eval_failed")
  else
    "$PY" scripts/p3_ev_offline.py || notes+=("p3_ev_offline_failed")
  fi
fi

if [[ "$WITH_LLM" -eq 1 ]]; then
  bash scripts/g2_formal_eval.sh --with-llm || blockers+=("live_heldout")
fi

if [[ "$WITH_NEO4J" -eq 1 ]]; then
  bash scripts/neo4j_regression.sh --allow-skip || true
else
  notes+=("neo4j_not_requested")
fi

if [[ "$WITH_QDRANT" -eq 1 ]]; then
  bash scripts/qdrant_regression.sh --allow-skip || true
else
  notes+=("qdrant_not_requested")
fi

if [[ "$WITH_LOAD" -eq 1 ]]; then
  "$PY" scripts/p3_load_http.py --n 20 --out reports/g3_offline/load_smoke.json || notes+=("load_smoke_failed")
else
  "$PY" scripts/p3_load_http.py --n 12 --out reports/g3_offline/load_smoke.json || true
fi

# Assemble status JSON via Python for robust merge
"$PY" - <<'PY'
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone

root = Path(".")
def load(p):
    path = root / p
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

held = load("reports/g2_heldout/g2_heldout_eval.json") or load("reports/g3_offline/heldout_eval.json")
load_smoke = load("reports/g3_offline/load_smoke.json")
neo = load("reports/neo4j_regression.json")
qdr = load("reports/qdrant_regression.json")
live_status = load("reports/g2_heldout_live_status.json")

# Dataset freeze hashes
def file_sha(p):
    path = root / p
    if not path.exists():
        return None
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:16]

g2_trend = None
if held:
    # support p3_ev shape + eval report shape (systems/summary)
    systems = held.get("systems") or {}
    agentic = held.get("agentic") if isinstance(held.get("agentic"), dict) else systems.get("agentic") or {}
    summary = held.get("summary") or {}
    delta = held.get("delta_accuracy_pp")
    if delta is None and "delta_agentic_minus_baseline" in held:
        delta = held["delta_agentic_minus_baseline"].get("accuracy_pp")
    if delta is None:
        delta = summary.get("accuracy_pp")
    recall = agentic.get("evidence_recall")
    if recall is None:
        recall = summary.get("agentic_evidence_recall")
    acc_a = agentic.get("accuracy_pct")
    if acc_a is None:
        acc_a = summary.get("agentic_accuracy_pct")
    n = agentic.get("n") or agentic.get("total")
    g2_trend = {
        "mode": held.get("mode") or "offline",
        "n": n,
        "delta_accuracy_pp": delta,
        "evidence_recall": recall,
        "agentic_accuracy_pct": acc_a,
        "g2_trend_offline_met": bool(
            delta is not None and float(delta) >= 15.0 and recall is not None and float(recall) >= 0.75
        ),
        "formal_g2_live": False,
        "formal_g3_claim": False,
    }

omit = []
pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
for line in pyproject.splitlines():
    if "agentic_graphrag" in line and line.strip().startswith('"*/'):
        omit.append(line.strip().strip(',').strip('"'))

status = {
    "schema_version": "1.0.0",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "corpus": "synthetic_pilot_caveat",
    "dataset": {
        "g2_all_sha16": file_sha("evals/datasets/g2_all.jsonl"),
        "g2_heldout_sha16": file_sha("evals/datasets/g2_heldout.jsonl"),
        "pilot_triples_sha16": file_sha("data/processed/pilot_triples.jsonl"),
        "heldout_n": 47,
        "gold_total": 200,
    },
    "g2_trend_offline": g2_trend,
    "g2_formal_live": live_status or {"status": "not_run"},
    "neo4j_regression": neo or {"status": "not_run"},
    "qdrant_regression": qdr or {"status": "not_run"},
    "load_smoke": {
        "p95_ms": (load_smoke or {}).get("latency_p95_ms"),
        "mode": (load_smoke or {}).get("mode"),
        "formal_ac4": False,
    } if load_smoke else {"status": "not_run"},
    "engineering": {
        "relation_embedder": "wired_when_allow_llm",
        "live_stores_env": "AGR_LIVE_STORES",
        "tenant_audit_authz": True,
        "cache_tenant_prefix": True,
        "llm_circuit": True,
        "canary_env": "AGR_CANARY_TENANTS",
        "prometheus_metrics": "/v1/metrics/prometheus",
    },
    "coverage_omit": omit,
    "blockers": [
        "product_real_domain_corpus",
        "live_llm_heldout_evidence",
        "production_p95_under_load",
        "deploy_side_alert_verification",
        "human_gold_review_signoff",
    ],
    "notes": [
        "Offline synthetic results must not be presented as product G2/G3 closeout.",
        "Heldout n≈47 is ~25% of ≥200 gold total (roadmap), not heldout≥200.",
    ],
}
out = root / "reports" / "ACCEPTANCE_STATUS.json"
out.write_text(json.dumps(status, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
md = root / "reports" / "ACCEPTANCE_STATUS.md"
lines = [
    "# Acceptance status",
    "",
    f"- generated: `{status['generated_at']}`",
    f"- corpus: **{status['corpus']}**",
    f"- gold total / heldout n: {status['dataset']['gold_total']} / {status['dataset']['heldout_n']}",
]
if g2_trend:
    lines += [
        "",
        "## G2 trend (offline heldout)",
        f"- Δ accuracy pp: **{g2_trend.get('delta_accuracy_pp')}**",
        f"- evidence recall: **{g2_trend.get('evidence_recall')}**",
        f"- met (+15pp / ≥0.75): **{g2_trend.get('g2_trend_offline_met')}**",
        f"- formal live claim: **{g2_trend.get('formal_g2_live')}**",
    ]
lines += ["", "## Blockers"] + [f"- {b}" for b in status["blockers"]]
lines += ["", "## Engineering flags"] + [f"- {k}: `{v}`" for k, v in status["engineering"].items()]
md.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Wrote {out}")
print(f"Wrote {md}")
PY

echo "=== Done → $STATUS ==="
