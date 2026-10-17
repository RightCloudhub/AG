"""Paired A/B comparison of two eval run reports (ARCH_OPTIMIZATION_V2 O-1).

Both runs must cover the same cases (same dataset, same seed graph, same LLM
cache). The verdict is driven by the exact McNemar p-value, not by the raw
accuracy delta, so a 1-case swing on 31 cases is reported as noise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentic_graphrag.eval.scoring import score_pair
from agentic_graphrag.eval.stats import (
    breakdown,
    category_of,
    mcnemar,
    percentile,
    template_of,
    wilson_interval,
)

SIGNIFICANCE = 0.05


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def load_cases(path: str | Path) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in load_rows(path)}


def correctness(rows: list[dict[str, Any]]) -> dict[str, bool]:
    """case_id → correct, scored with the production scorer."""
    out: dict[str, bool] = {}
    for row in rows:
        pred = row.get("prediction") or ""
        if row.get("error") and not pred:
            out[str(row.get("case_id"))] = False
            continue
        out[str(row.get("case_id"))] = bool(score_pair(pred, row.get("gold") or "")["correct"])
    return out


def run_summary(rows: list[dict[str, Any]], cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    results = correctness(rows)
    correct = sum(results.values())
    lo, hi = wilson_interval(correct, len(results))
    latencies = [float(r.get("latency_ms") or 0) for r in rows]
    calls = [float((r.get("cost") or {}).get("llm_calls") or 0) for r in rows]
    return {
        "correct": correct,
        "total": len(results),
        "accuracy": round(correct / len(results), 4) if results else 0.0,
        "ci95": [round(lo, 4), round(hi, 4)],
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        "llm_calls_mean": round(sum(calls) / len(calls), 3) if calls else 0.0,
        "by_category": _groups(results, cases, category_of),
        "by_template": _groups(results, cases, template_of),
    }


def _groups(results, cases, key) -> dict[str, Any]:
    return {k: v.to_dict() for k, v in breakdown(results, cases, key).items()}


def compare_runs(
    base_rows: list[dict[str, Any]],
    cand_rows: list[dict[str, Any]],
    cases: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Full A/B report; ``verdict`` is better / worse / no_significant_difference."""
    base_ok = correctness(base_rows)
    cand_ok = correctness(cand_rows)
    test = mcnemar(base_ok, cand_ok)
    if test.p_value >= SIGNIFICANCE:
        verdict = "no_significant_difference"
    else:
        verdict = "better" if test.b_only > test.a_only else "worse"
    flips = sorted(i for i in set(base_ok) & set(cand_ok) if base_ok[i] != cand_ok[i])
    return {
        "base": run_summary(base_rows, cases),
        "candidate": run_summary(cand_rows, cases),
        "paired": test.to_dict(),
        "verdict": verdict,
        "flipped_cases": [{"case_id": i, "candidate_correct": cand_ok[i]} for i in flips],
    }


def format_text(report: dict[str, Any]) -> str:
    b, c, p = report["base"], report["candidate"], report["paired"]
    lines = [
        f"base      {b['correct']}/{b['total']} acc={b['accuracy']:.3f} ci95={b['ci95']}",
        f"candidate {c['correct']}/{c['total']} acc={c['accuracy']:.3f} ci95={c['ci95']}",
        f"paired    candidate_only={p['b_only']} base_only={p['a_only']} p={p['p_value']:.4f}",
        f"latency   p95 {b['latency_p95_ms']:.0f}ms -> {c['latency_p95_ms']:.0f}ms",
        f"llm_calls mean {b['llm_calls_mean']} -> {c['llm_calls_mean']}",
        f"verdict   {report['verdict']}",
    ]
    return "\n".join(lines)
