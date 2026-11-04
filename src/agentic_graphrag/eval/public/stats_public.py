"""Statistics for the public comparison (B-09): variance, clustered CIs, paired tests.

Repeated trials of one question across run seeds are *not* independent samples:
every interval here resamples question IDs (cluster bootstrap), averaging a
question's seeds first. McNemar is applied separately per seed.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from agentic_graphrag.eval.stats import mcnemar

PairKey = tuple[str, int]  # (question_id, run_seed)
DEFAULT_ALPHA = 0.05


def describe(values: Sequence[float]) -> dict[str, float | int | None]:
    """Mean with *sample* variance/stddev (``None`` below two values)."""
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "variance": None, "stddev": None}
    var = statistics.variance(values) if n > 1 else None
    return {
        "n": n,
        "mean": statistics.fmean(values),
        "variance": var,
        "stddev": math.sqrt(var) if var is not None else None,
    }


def percentile(values: Sequence[float], p: float) -> float | None:
    """Nearest-rank percentile; ``None`` for no data (never a fake zero)."""
    data = sorted(values)
    if not data:
        return None
    idx = min(len(data) - 1, max(0, math.ceil(p / 100.0 * len(data)) - 1))
    return float(data[idx])


def _cluster_means(values: Mapping[PairKey, float]) -> dict[str, float]:
    by_q: dict[str, list[float]] = defaultdict(list)
    for (qid, _seed), value in values.items():
        by_q[qid].append(value)
    return {qid: statistics.fmean(v) for qid, v in sorted(by_q.items())}


def _bootstrap(
    cluster_values: list[float], *, resamples: int, seed: int, alpha: float
) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(cluster_values)
    means = sorted(statistics.fmean(rng.choices(cluster_values, k=n)) for _ in range(resamples))
    lo = means[int(math.floor(alpha / 2 * (resamples - 1)))]
    hi = means[int(math.ceil((1 - alpha / 2) * (resamples - 1)))]
    return lo, hi


def cluster_bootstrap_ci(
    values: Mapping[PairKey, float],
    *,
    resamples: int,
    seed: int,
    alpha: float = DEFAULT_ALPHA,
) -> dict[str, Any] | None:
    """Mean and percentile CI with question-level clustering across seeds."""
    clusters = list(_cluster_means(values).values())
    if not clusters:
        return None
    lo, hi = _bootstrap(clusters, resamples=resamples, seed=seed, alpha=alpha)
    return {"mean": statistics.fmean(clusters), "ci": [lo, hi], "clusters": len(clusters)}


def paired_bootstrap(
    a: Mapping[PairKey, float],
    b: Mapping[PairKey, float],
    *,
    resamples: int,
    seed: int,
    alpha: float = DEFAULT_ALPHA,
) -> dict[str, Any] | None:
    """CI of mean(b - a) over trials present in both, clustered by question."""
    shared = sorted(set(a) & set(b))
    diffs = {k: b[k] - a[k] for k in shared}
    result = cluster_bootstrap_ci(diffs, resamples=resamples, seed=seed, alpha=alpha)
    if result is None:
        return None
    lo, hi = result["ci"]
    return {**result, "pairs": len(shared), "excludes_zero": lo > 0 or hi < 0}


def mcnemar_by_seed(
    a: Mapping[PairKey, bool], b: Mapping[PairKey, bool]
) -> dict[int, dict[str, Any]]:
    """Exact McNemar per run seed on binary correctness (a = baseline, b = candidate)."""
    seeds = sorted({s for _q, s in a} & {s for _q, s in b})
    out: dict[int, dict[str, Any]] = {}
    for seed in seeds:
        sa = {q: v for (q, s), v in a.items() if s == seed}
        sb = {q: v for (q, s), v in b.items() if s == seed}
        out[seed] = mcnemar(sa, sb).to_dict()
    return out


def risk_coverage(points: Sequence[tuple[float, bool]]) -> list[dict[str, float]]:
    """Answer the top-confidence fraction; risk = error rate among answered."""
    ranked = sorted(points, key=lambda p: -p[0])
    curve: list[dict[str, float]] = []
    wrong = 0
    for i, (score, correct) in enumerate(ranked, start=1):
        wrong += 0 if correct else 1
        curve.append({"threshold": score, "coverage": i / len(ranked), "risk": wrong / i})
    return curve
