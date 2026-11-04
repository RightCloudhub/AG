"""B-00 feasibility spike report: schema fit, graph coverage, F1, cost → decision field."""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from agentic_graphrag.eval.public import NOT_EXECUTED
from agentic_graphrag.eval.public.enums import ArmName, DatasetName
from agentic_graphrag.eval.public.manifest import RunManifest, manifest_summary
from agentic_graphrag.eval.public.manifest_models import SpikeRules
from agentic_graphrag.eval.public.stats_public import percentile
from agentic_graphrag.eval.public.trial_scores import ScoredTrial

SPIKE_ARMS: tuple[ArmName, ArmName] = (ArmName.AG_SINGLE_TIER, ArmName.PURE_VECTOR)
DECISIONS: tuple[str, ...] = ("continue", "revise_schema", "rescope")
P95 = 95.0


def _query_side(rows: Sequence[ScoredTrial]) -> dict[str, Any]:
    if not rows:
        return {"status": NOT_EXECUTED}
    tokens = [float(r.tokens) for r in rows if r.tokens is not None]
    latency = [r.latency_ms for r in rows if r.latency_ms is not None]
    return {
        "status": "executed",
        "n": len(rows),
        "f1_squad": statistics.fmean(r.f1 for r in rows),
        "tokens_per_query": statistics.fmean(tokens) if tokens else None,
        "token_coverage": len(tokens) / len(rows),
        "latency_p95_ms": percentile(latency, P95),
    }


def _suggest(entry: dict[str, Any], rules: SpikeRules) -> str:
    ag, vec = entry["arms"][ArmName.AG_SINGLE_TIER.value], entry["arms"][ArmName.PURE_VECTOR.value]
    idx = entry["indexing"]
    if NOT_EXECUTED in (ag.get("status"), vec.get("status"), idx.get("status")):
        return NOT_EXECUTED
    rejection, coverage = idx.get("schema_rejection_rate"), idx.get("graph_coverage")
    if rejection is None or coverage is None:
        return "revise_schema"
    if rejection > rules.max_schema_rejection_rate or coverage < rules.min_graph_coverage:
        return "revise_schema"
    if ag["f1_squad"] - vec["f1_squad"] < rules.min_f1_delta_vs_vector:
        return "rescope"
    return "continue"


def spike_report(
    manifest: RunManifest,
    scored: Sequence[ScoredTrial],
    *,
    indexing: Mapping[DatasetName, dict[str, Any]],
    decision: str | None = None,
) -> dict[str, Any]:
    """``indexing[ds]`` carries schema_rejection_rate, graph_coverage and index usage."""
    if decision is not None and decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    rules = manifest.spike_rules or SpikeRules()
    datasets: list[dict[str, Any]] = []
    for spec in manifest.datasets:
        arms = {
            arm.value: _query_side([r for r in scored if r.arm is arm and r.dataset is spec.name])
            for arm in SPIKE_ARMS
        }
        idx = dict(indexing.get(spec.name) or {"status": NOT_EXECUTED})
        entry: dict[str, Any] = {"dataset": spec.name.value, "arms": arms, "indexing": idx}
        entry["suggested"] = _suggest(entry, rules)
        datasets.append(entry)
    suggestions = {d["suggested"] for d in datasets}
    executed = NOT_EXECUTED not in suggestions
    return {
        "manifest": manifest_summary(manifest),
        "status": "executed" if executed else NOT_EXECUTED,
        "rules": rules.model_dump(mode="json"),
        "datasets": datasets,
        "suggested_decision": _overall(suggestions) if executed else NOT_EXECUTED,
        # Set by a person after reading the spike; never inferred automatically.
        "decision": decision or ("pending operator decision" if executed else NOT_EXECUTED),
    }


def _overall(suggestions: set[str]) -> str:
    for worst in ("rescope", "revise_schema"):
        if worst in suggestions:
            return worst
    return "continue"
