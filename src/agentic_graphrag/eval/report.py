"""One-click comparison report rendering (P2-EV-04 / FR-OP-04).

Builds and writes JSON (+ optional Markdown) comparison reports from run
artifacts. Metric computation lives in :mod:`agentic_graphrag.eval.metrics`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentic_graphrag.eval.metrics import (
    SystemMetrics,
    evidence_recall_for_row,
    fabrication_rate,
    load_cases,
    load_jsonl,
    score_system_rows,
)

# Backward-compatible re-exports (tests / CLI import from report).
__all__ = [
    "SystemMetrics",
    "build_comparison_report",
    "evidence_recall_for_row",
    "fabrication_rate",
    "render_markdown",
    "score_system_rows",
    "write_comparison_report",
]


def build_comparison_report(
    *,
    agentic_path: Path,
    baseline_path: Path | None = None,
    cases_path: Path | None = None,
) -> dict[str, Any]:
    cases_by_id = load_cases(cases_path) if cases_path else {}
    agentic_rows = load_jsonl(agentic_path)
    agentic = score_system_rows(agentic_rows, system="agentic", cases_by_id=cases_by_id)

    systems: dict[str, Any] = {"agentic": agentic.to_dict()}
    delta: dict[str, Any] = {}
    if baseline_path and baseline_path.exists():
        baseline_rows = load_jsonl(baseline_path)
        baseline = score_system_rows(baseline_rows, system="baseline", cases_by_id=cases_by_id)
        systems["baseline"] = baseline.to_dict()
        delta = {
            "accuracy_pp": round((agentic.accuracy - baseline.accuracy) * 100, 2),
            "evidence_recall_pp": (
                round((agentic.evidence_recall - baseline.evidence_recall) * 100, 2)
                if agentic.evidence_recall is not None and baseline.evidence_recall is not None
                else None
            ),
            "latency_p50_ms_delta": round(agentic.latency_p50_ms - baseline.latency_p50_ms, 2),
            "latency_p95_ms_delta": round(agentic.latency_p95_ms - baseline.latency_p95_ms, 2),
            "cost_tokens_mean_delta": round(
                agentic.cost_tokens_mean - baseline.cost_tokens_mean, 2
            ),
            "cost_llm_calls_mean_delta": round(
                agentic.cost_llm_calls_mean - baseline.cost_llm_calls_mean, 2
            ),
        }

    badcases = [c for c in agentic.cases if not c["correct"]]
    return {
        "schema_version": "1.0.0",
        "task": "P2-EV-04",
        "inputs": {
            "agentic": str(agentic_path),
            "baseline": str(baseline_path) if baseline_path else None,
            "cases": str(cases_path) if cases_path else None,
        },
        "systems": systems,
        "delta_agentic_minus_baseline": delta,
        "badcases": badcases,
        "summary": {
            "agentic_accuracy_pct": systems["agentic"]["accuracy_pct"],
            "baseline_accuracy_pct": (
                systems["baseline"]["accuracy_pct"] if "baseline" in systems else None
            ),
            "accuracy_pp": delta.get("accuracy_pp"),
            "agentic_evidence_recall": systems["agentic"]["evidence_recall"],
            "agentic_latency_p50_ms": systems["agentic"]["latency_p50_ms"],
            "agentic_latency_p95_ms": systems["agentic"]["latency_p95_ms"],
            "agentic_cost_tokens_mean": systems["agentic"]["cost_tokens_mean"],
            "fabrication_rate": systems["agentic"]["fabrication_rate"],
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    s = report.get("summary") or {}
    lines = [
        "# Evaluation comparison report (P2-EV-04)",
        "",
        "## Summary",
        "",
        f"- Agentic accuracy: **{s.get('agentic_accuracy_pct')}%**",
        f"- Baseline accuracy: **{s.get('baseline_accuracy_pct')}%**",
        f"- Accuracy delta (pp): **{s.get('accuracy_pp')}**",
        f"- Agentic evidence recall: **{s.get('agentic_evidence_recall')}**",
        f"- Agentic latency P50/P95 (ms): **{s.get('agentic_latency_p50_ms')}** / "
        f"**{s.get('agentic_latency_p95_ms')}**",
        f"- Agentic mean tokens: **{s.get('agentic_cost_tokens_mean')}**",
        f"- Fabrication rate: **{s.get('fabrication_rate')}**",
        "",
        "## Systems",
        "",
    ]
    for name, metrics in (report.get("systems") or {}).items():
        lines.append(f"### {name}")
        lines.append("")
        lines.append(f"- total/correct: {metrics.get('total')}/{metrics.get('correct')}")
        lines.append(f"- accuracy: {metrics.get('accuracy_pct')}%")
        lines.append(f"- evidence_recall: {metrics.get('evidence_recall')}")
        p50, p95 = metrics.get("latency_p50_ms"), metrics.get("latency_p95_ms")
        lines.append(f"- latency P50/P95: {p50} / {p95} ms")
        lines.append(
            f"- cost mean tokens/calls: {metrics.get('cost_tokens_mean')} / "
            f"{metrics.get('cost_llm_calls_mean')}"
        )
        lines.append(f"- by_hops: `{json.dumps(metrics.get('by_hops') or {}, ensure_ascii=False)}`")
        lines.append("")
    bad = report.get("badcases") or []
    lines.append(f"## Badcases ({len(bad)})")
    lines.append("")
    for b in bad[:50]:
        lines.append(f"- `{b.get('case_id')}` gold=`{b.get('gold')}` pred=`{b.get('prediction')}`")
    lines.append("")
    return "\n".join(lines)


def write_comparison_report(
    report: dict[str, Any],
    out_dir: Path,
    *,
    stem: str = "eval_comparison",
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": json_path, "md": md_path}
