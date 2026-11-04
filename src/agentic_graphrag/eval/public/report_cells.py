"""Cell-level metrics (arm × dataset × seed) and cross-seed summaries (B-09)."""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from typing import Any

from agentic_graphrag.eval.public.enums import TrialStatus
from agentic_graphrag.eval.public.stats_public import (
    cluster_bootstrap_ci,
    describe,
    percentile,
)
from agentic_graphrag.eval.public.trial_scores import ScoredTrial, recall_keys

P50 = 50.0
P95 = 95.0
SUMMARY_METRICS: tuple[str, ...] = ("em", "f1", "contain")
_FAILED = frozenset({TrialStatus.ERROR, TrialStatus.INTERRUPTED})


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _known(
    rows: Sequence[ScoredTrial], get: Callable[[ScoredTrial], float | None]
) -> dict[str, Any]:
    values = [v for v in (get(r) for r in rows) if v is not None]
    return {
        "mean": _mean(values),
        "coverage": len(values) / len(rows) if rows else None,
    }


def _rate(rows: Sequence[ScoredTrial], pred: Callable[[ScoredTrial], bool]) -> float | None:
    return sum(1 for r in rows if pred(r)) / len(rows) if rows else None


def answer_metrics(rows: Sequence[ScoredTrial]) -> dict[str, Any]:
    """Unconditional accuracy over all trials plus selective accuracy *with* coverage."""
    selected = [r for r in rows if not r.abstained]
    native = [r for r in rows if r.native_status is not None]
    return {
        "em_squad": _mean([r.em for r in rows]),
        "f1_squad": _mean([r.f1 for r in rows]),
        "contain_normalized": _mean([r.contain for r in rows]),
        "accuracy_em_all_trials": _mean([r.em for r in rows]),
        "selective_em": _mean([r.em for r in selected]),  # None when coverage is zero
        "selective_coverage": len(selected) / len(rows) if rows else None,
        "abstention_rate": _rate(rows, lambda r: r.abstained),
        "native_partial_rate": _rate(native, lambda r: r.native_status == "partial"),
        "native_detector_agreement": _rate(native, lambda r: r.native_abstained == r.abstained),
    }


def evidence_metrics(rows: Sequence[ScoredTrial], cutoffs: Sequence[int]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in recall_keys(cutoffs):
        values = [r.recall.get(key) for r in rows]
        measured = [v for v in values if v is not None]
        out[key] = {"mean": _mean(measured), "measured": len(measured)}
    kinds = sorted({k for r in rows for k in r.alignment_coverage})
    out["alignment_coverage"] = {k: _alignment(rows, k) for k in kinds}
    return out


def _alignment(rows: Sequence[ScoredTrial], kind: str) -> dict[str, Any]:
    def get(r: ScoredTrial) -> float | None:
        return r.alignment_coverage.get(kind)

    return _known(rows, get)


def cost_metrics(rows: Sequence[ScoredTrial]) -> dict[str, Any]:
    latencies = [r.latency_ms for r in rows if r.latency_ms is not None]
    return {
        "latency_p50_ms": percentile(latencies, P50),
        "latency_p95_ms": percentile(latencies, P95),
        "latency_measured": len(latencies),
        "timeout_rate": _rate(rows, lambda r: r.status is TrialStatus.TIMEOUT),
        "error_rate": _rate(rows, lambda r: r.status in _FAILED),
        "tokens_per_query": _known(rows, lambda r: r.tokens),
        "llm_calls_per_query": _known(rows, lambda r: r.llm_calls),
        "money_per_query": _known(rows, lambda r: r.money),
        "attempts_per_trial": _mean([float(r.attempts) for r in rows]),
        "interrupted_attempts": sum(r.interrupted_attempts for r in rows),
    }


def cell_metrics(rows: Sequence[ScoredTrial], cutoffs: Sequence[int]) -> dict[str, Any]:
    return {
        "n": len(rows),
        **answer_metrics(rows),
        "evidence": evidence_metrics(rows, cutoffs),
        "cost": cost_metrics(rows),
    }


def seed_summary(rows: Sequence[ScoredTrial], *, resamples: int, seed: int) -> dict[str, Any]:
    """Across-seed mean/variance of per-seed means + question-clustered bootstrap CI."""
    out: dict[str, Any] = {}
    seeds = sorted({r.run_seed for r in rows})
    for metric in SUMMARY_METRICS:
        per_seed = [
            statistics.fmean(getattr(r, metric) for r in rows if r.run_seed == s) for s in seeds
        ]
        values = {(r.question_id, r.run_seed): float(getattr(r, metric)) for r in rows}
        out[metric] = {
            "across_seeds": describe(per_seed),
            "clustered": cluster_bootstrap_ci(values, resamples=resamples, seed=seed),
        }
    return out
