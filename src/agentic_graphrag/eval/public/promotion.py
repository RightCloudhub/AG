"""B-10 promotion report: predeclared bounds → go / no-go, or "Not Executed"."""

from __future__ import annotations

import statistics
from typing import Any

from agentic_graphrag.eval.public import NOT_EXECUTED
from agentic_graphrag.eval.public.enums import ArmName
from agentic_graphrag.eval.public.manifest import RunManifest
from agentic_graphrag.eval.public.manifest_models import PromotionBounds

PP = 100.0


def _arm_mean(
    report: dict[str, Any], arm: ArmName, dataset: str, path: tuple[str, ...]
) -> float | None:
    values: list[float] = []
    for cell in report["cells"]:
        if cell["arm"] != arm.value or cell["dataset"] != dataset or cell["status"] == NOT_EXECUTED:
            continue
        value: Any = cell
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        if value is not None:
            values.append(float(value))
    return statistics.fmean(values) if values else None


def _significant(paired: dict[str, Any], alpha: float) -> bool:
    em = paired.get("em_diff") or {}
    ci_positive = bool(em.get("ci")) and em["ci"][0] > 0
    seeds = paired.get("mcnemar_em_by_seed") or {}
    all_seeds = bool(seeds) and all(
        r["p_value"] < alpha and r["b_only"] > r["a_only"] for r in seeds.values()
    )
    return ci_positive or all_seeds


def _check(name: str, value: float | None, bound: float | None, *, upper: bool) -> dict[str, Any]:
    if bound is None:
        return {"check": name, "value": value, "bound": None, "pass": None}
    passed = None if value is None else (value <= bound if upper else value >= bound)
    return {"check": name, "value": value, "bound": bound, "pass": passed}


def _diff(a: float | None, b: float | None, scale: float = PP) -> float | None:
    return None if a is None or b is None else (b - a) * scale


def _dataset_checks(
    report: dict[str, Any], dataset: str, bounds: PromotionBounds, paired: dict[str, Any]
) -> list[dict[str, Any]]:
    def mean(arm: ArmName, *path: str) -> float | None:
        return _arm_mean(report, arm, dataset, path)

    single, reg = ArmName.AG_SINGLE_TIER, ArmName.AG_REGISTRY
    gain = _diff(mean(single, "em_squad"), mean(reg, "em_squad"))
    cov_drop = _diff(mean(reg, "selective_coverage"), mean(single, "selective_coverage"))
    sel_single, sel_reg = mean(single, "selective_em"), mean(reg, "selective_em")
    risk_up = None if sel_single is None or sel_reg is None else (sel_single - sel_reg) * PP
    tok_single = mean(single, "cost", "tokens_per_query", "mean")
    tok_reg = mean(reg, "cost", "tokens_per_query", "mean")
    ratio = tok_reg / tok_single if tok_single and tok_reg is not None else None
    # A claimed gain needs statistical evidence; a non-inferiority bound (<= 0) does not.
    claims_gain = (bounds.min_accuracy_gain_pp or 0.0) > 0
    significance = _significant(paired, bounds.alpha) if claims_gain else None
    return [
        _check("accuracy_gain_pp", gain, bounds.min_accuracy_gain_pp, upper=False),
        {"check": "significant_gain", "pass": significance},
        _check("coverage_drop_pp", cov_drop, bounds.max_coverage_drop_pp, upper=True),
        _check(
            "selective_risk_increase_pp", risk_up, bounds.max_selective_risk_increase_pp, upper=True
        ),
        _check("token_ratio", ratio, bounds.max_token_ratio, upper=True),
        _check(
            "deep_p95_ms", mean(reg, "cost", "latency_p95_ms"), bounds.max_deep_p95_ms, upper=True
        ),
    ]


def promotion_report(manifest: RunManifest, report: dict[str, Any]) -> dict[str, Any]:
    bounds = manifest.promotion
    base: dict[str, Any] = {"bounds": bounds.model_dump(mode="json"), "datasets": []}
    if not bounds.declared:
        return {**base, "decision": NOT_EXECUTED, "reason": "promotion bounds not predeclared"}
    paired = {p["dataset"]: p for p in report["paired_ag"]}
    if any(p["status"] == NOT_EXECUTED for p in paired.values()) or not paired:
        return {**base, "decision": NOT_EXECUTED, "reason": "paired AG results missing"}
    regressions: list[str] = []
    for dataset, p in paired.items():
        checks = _dataset_checks(report, dataset, bounds, p)
        failed = [c["check"] for c in checks if c["pass"] is False or (
            c["pass"] is None and c.get("bound") is not None
        )]  # fmt: skip
        base["datasets"].append({"dataset": dataset, "checks": checks, "failed": failed})
        regressions.extend(f"{dataset}: {name}" for name in failed)
    decision = "go" if not regressions else "no-go"
    return {**base, "decision": decision, "regressions": regressions}
