"""B-12 cost extrapolation from spike/pilot ledgers with the predeclared step-down order."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from agentic_graphrag.eval.public import NOT_EXECUTED
from agentic_graphrag.eval.public.enums import AG_PAIR, ArmName, DatasetName, StepDownLevel
from agentic_graphrag.eval.public.manifest import RunManifest
from agentic_graphrag.eval.public.planning import STEP_DOWN_LABELS, plan_trials

TOKENS_PER_MILLION = 1_000_000
SHARED_INDEX_ARM = ArmName.AG_SINGLE_TIER  # the AG pair shares one index; count it once


class UnitCosts(BaseModel):
    """Measured means from spike/pilot ledgers; ``None`` = not measured (Not Executed)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    query_tokens: dict[ArmName, float | None]
    index_tokens_per_passage: dict[ArmName, float | None]
    money_per_million_tokens: float | None = None


def _query_tokens(units: UnitCosts, counts: Counter[ArmName]) -> float | None:
    total = 0.0
    for arm, n in counts.items():
        per = units.query_tokens.get(arm)
        if per is None:
            return None
        total += per * n
    return total


def _index_tokens(
    m: RunManifest, units: UnitCosts, passages: Mapping[DatasetName, int]
) -> float | None:
    total = 0.0
    for arm in m.arms:
        if arm.name in AG_PAIR and arm.name is not SHARED_INDEX_ARM:
            continue
        per = units.index_tokens_per_passage.get(arm.name)
        if per is None:
            return None
        total += per * sum(passages.get(d.name, 0) for d in m.datasets)
    return total


def _level_estimate(
    m: RunManifest,
    units: UnitCosts,
    level: StepDownLevel,
    sizes: tuple[Mapping[DatasetName, int], Mapping[DatasetName, int]],
) -> dict[str, Any]:
    questions, passages = sizes
    ids = {d: [f"q{i:06d}" for i in range(n)] for d, n in questions.items()}
    keys = plan_trials(m, ids, level=level)
    counts: Counter[ArmName] = Counter(k.arm for k in keys)
    q_tokens = _query_tokens(units, counts)
    i_tokens = _index_tokens(m, units, passages)
    total = None if q_tokens is None or i_tokens is None else q_tokens + i_tokens
    price = units.money_per_million_tokens
    money = None if total is None or price is None else total * price / TOKENS_PER_MILLION
    return {
        "level": int(level),
        "label": STEP_DOWN_LABELS[level],
        "trials": len(keys),
        "trials_by_arm": {a.value: n for a, n in sorted(counts.items())},
        "query_tokens": q_tokens,
        "index_tokens": i_tokens,
        "total_tokens": total,
        "money": money,
    }


def _fits(m: RunManifest, est: dict[str, Any]) -> bool | None:
    ceiling = m.ceiling
    if est["total_tokens"] is None:
        return None
    if ceiling.provider_tokens is not None and est["total_tokens"] > ceiling.provider_tokens:
        return False
    if ceiling.money is not None:
        if est["money"] is None:
            return None
        return bool(est["money"] <= ceiling.money)
    return True


def estimate_run(
    manifest: RunManifest,
    units: UnitCosts,
    *,
    questions: Mapping[DatasetName, int],
    passages: Mapping[DatasetName, int],
) -> dict[str, Any]:
    """Estimate each level in the predeclared order and pick the first that fits."""
    levels = [_level_estimate(manifest, units, lvl, (questions, passages)) for lvl in StepDownLevel]
    declared = manifest.ceiling.money is not None or manifest.ceiling.provider_tokens is not None
    chosen: Any = NOT_EXECUTED
    reason = "ceiling not declared" if not declared else "unit costs not measured"
    if declared:
        for est in levels:
            fit = _fits(manifest, est)
            if fit is None:
                break
            if fit:
                chosen, reason = est["level"], "first level within the ceiling"
                break
        else:
            reason = "exceeds the ceiling at every level: stop and decide before the run"
    return {"levels": levels, "chosen_level": chosen, "reason": reason}


def units_from_means(
    query: Mapping[ArmName, float | None],
    index: Mapping[ArmName, float | None],
    *,
    money_per_million_tokens: float | None = None,
) -> UnitCosts:
    return UnitCosts(
        query_tokens=dict(query),
        index_tokens_per_passage=dict(index),
        money_per_million_tokens=money_per_million_tokens,
    )
