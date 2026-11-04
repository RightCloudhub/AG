"""Public benchmark report assembly (B-09, B-12): JSON first, Markdown via report_md.

Any arm/dataset/seed without results prints "Not Executed"; the B-12 step-down
level and the protocol revision status are part of every report.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Any

from agentic_graphrag.eval.public import NOT_EXECUTED
from agentic_graphrag.eval.public.adapters_base import PrepareReport
from agentic_graphrag.eval.public.enums import ArmName, DatasetName
from agentic_graphrag.eval.public.journal_check import JournalValidation
from agentic_graphrag.eval.public.manifest import RunManifest, manifest_summary
from agentic_graphrag.eval.public.planning import STEP_DOWN_LABELS
from agentic_graphrag.eval.public.report_cells import cell_metrics, seed_summary
from agentic_graphrag.eval.public.scoring import CONTAIN_METRIC, EM_METRIC, F1_METRIC, detector_for
from agentic_graphrag.eval.public.stats_public import (
    mcnemar_by_seed,
    paired_bootstrap,
    risk_coverage,
)
from agentic_graphrag.eval.public.trial_scores import RETRIEVED_ALL, ScoredTrial
from agentic_graphrag.eval.public.trials import TrialKey

MACRO_METRICS: tuple[str, ...] = (EM_METRIC, F1_METRIC, CONTAIN_METRIC, "selective_em")
STANDING_DEVIATIONS: tuple[str, ...] = (
    "AG composer emits long-form answers; EM/F1 use the shared 'Answer:' span rule, so "
    "contain_normalized is reported alongside (comparability deviation).",
    "BudgetTracker prompt_tokens include query-embedding tokens; embed_tokens stay unknown.",
    "External arms report text contexts; their recall depends on audited text alignment.",
    "AG evidence catalog drops Candidate.citations; graph path evidence may stay unaligned.",
    "Provider seed control is not supported: run seeds are independent repeats.",
)


def _rows(
    scored: Sequence[ScoredTrial], arm: ArmName, dataset: DatasetName, seed: int | None = None
) -> list[ScoredTrial]:
    return [
        r
        for r in scored
        if r.arm is arm and r.dataset is dataset and (seed is None or r.run_seed == seed)
    ]


def _planned_cells(planned: Sequence[TrialKey]) -> dict[tuple[ArmName, DatasetName, int], int]:
    cells: dict[tuple[ArmName, DatasetName, int], int] = {}
    for key in planned:
        cell = (key.arm, key.dataset, key.run_seed)
        cells[cell] = cells.get(cell, 0) + 1
    return cells


def _cells(
    m: RunManifest, scored: Sequence[ScoredTrial], planned: Sequence[TrialKey]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for (arm, dataset, seed), expected in sorted(_planned_cells(planned).items()):
        rows = _rows(scored, arm, dataset, seed)
        base = {"arm": arm.value, "dataset": dataset.value, "seed": seed, "planned": expected}
        if not rows:
            out.append({**base, "status": NOT_EXECUTED})
            continue
        status = "complete" if len(rows) == expected else f"partial {len(rows)}/{expected}"
        out.append({**base, "status": status, **cell_metrics(rows, m.context.recall_cutoffs)})
    return out


def _summaries(m: RunManifest, scored: Sequence[ScoredTrial]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for dataset in m.datasets:
        for arm in m.arms:
            rows = _rows(scored, arm.name, dataset.name)
            entry: dict[str, Any] = {"arm": arm.name.value, "dataset": dataset.name.value}
            if not rows:
                out.append({**entry, "status": NOT_EXECUTED})
                continue
            stats = seed_summary(rows, resamples=m.bootstrap_resamples, seed=m.bootstrap_seed)
            out.append({**entry, "status": "executed", **stats})
    return out


def _macro(m: RunManifest, cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Macro average of per-dataset means; Not Executed unless every dataset has results."""
    out: list[dict[str, Any]] = []
    for arm in m.arms:
        entry: dict[str, Any] = {"arm": arm.name.value}
        per_dataset: dict[str, list[float]] = {k: [] for k in MACRO_METRICS}
        for dataset in m.datasets:
            ds_cells = [
                c
                for c in cells
                if c["arm"] == arm.name.value
                and c["dataset"] == dataset.name.value
                and c["status"] != NOT_EXECUTED
            ]
            for metric in MACRO_METRICS:
                values = [c[metric] for c in ds_cells if c.get(metric) is not None]
                if values:
                    per_dataset[metric].append(statistics.fmean(values))
        for metric, values in per_dataset.items():
            full = len(values) == len(m.datasets)
            entry[metric] = statistics.fmean(values) if full else NOT_EXECUTED
        out.append(entry)
    return out


def _keyed(rows: Sequence[ScoredTrial], attr: str) -> dict[tuple[str, int], float]:
    out: dict[tuple[str, int], float] = {}
    for r in rows:
        value = r.recall.get(attr) if attr in r.recall else getattr(r, attr, None)
        if value is not None:
            out[(r.question_id, r.run_seed)] = float(value)
    return out


def _paired_ag(m: RunManifest, scored: Sequence[ScoredTrial]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for dataset in m.datasets:
        a = _rows(scored, ArmName.AG_SINGLE_TIER, dataset.name)
        b = _rows(scored, ArmName.AG_REGISTRY, dataset.name)
        entry: dict[str, Any] = {"dataset": dataset.name.value}
        if not a or not b:
            out.append({**entry, "status": NOT_EXECUTED})
            continue
        boot: dict[str, Any] = {
            "resamples": m.bootstrap_resamples,
            "seed": m.bootstrap_seed,
            "alpha": m.promotion.alpha,
        }
        correct_a = {(r.question_id, r.run_seed): r.em >= 1.0 for r in a}
        correct_b = {(r.question_id, r.run_seed): r.em >= 1.0 for r in b}
        out.append(
            {
                **entry,
                "status": "executed",
                "baseline": ArmName.AG_SINGLE_TIER.value,
                "candidate": ArmName.AG_REGISTRY.value,
                "mcnemar_em_by_seed": mcnemar_by_seed(correct_a, correct_b),
                "f1_diff": paired_bootstrap(_keyed(a, "f1"), _keyed(b, "f1"), **boot),
                "em_diff": paired_bootstrap(_keyed(a, "em"), _keyed(b, "em"), **boot),
                "recall_all_diff": paired_bootstrap(
                    _keyed(a, RETRIEVED_ALL), _keyed(b, RETRIEVED_ALL), **boot
                ),
                "latency_ms_diff": paired_bootstrap(
                    _keyed(a, "latency_ms"), _keyed(b, "latency_ms"), **boot
                ),
                "tokens_diff": paired_bootstrap(_keyed(a, "tokens"), _keyed(b, "tokens"), **boot),
            }
        )
    return out


def _risk_coverage(m: RunManifest, scored: Sequence[ScoredTrial]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for arm in m.arms:
        rows = [r for r in scored if r.arm is arm.name]
        points = [(r.confidence, r.em >= 1.0) for r in rows if r.confidence is not None]
        if not rows:
            out[arm.name.value] = NOT_EXECUTED
        elif not points:
            out[arm.name.value] = "not available (arm emits no confidence score)"
        else:
            out[arm.name.value] = {
                "score_coverage": len(points) / len(rows),
                "curve": risk_coverage(points),
            }
    return out


def _index_costs(m: RunManifest, prepared: Sequence[PrepareReport]) -> list[dict[str, Any]]:
    by_arm = {(p.arm, p.stats.get("dataset")): p for p in prepared}
    out: list[dict[str, Any]] = []
    for dataset in m.datasets:
        for arm in m.arms:
            rep = by_arm.get((arm.name, dataset.name.value))
            entry = {"arm": arm.name.value, "dataset": dataset.name.value}
            if rep is None:
                out.append({**entry, "status": NOT_EXECUTED})
            else:
                out.append(
                    {
                        **entry,
                        "status": "executed",
                        "passages": rep.passages,
                        "usage": rep.usage.model_dump(mode="json"),
                        "shared_index": bool(rep.stats.get("shared_index")),
                    }
                )
    return out


def build_report(
    manifest: RunManifest,
    scored: Sequence[ScoredTrial],
    *,
    planned: Sequence[TrialKey],
    validation: JournalValidation | None = None,
    prepared: Sequence[PrepareReport] = (),
) -> dict[str, Any]:
    cells = _cells(manifest, scored, planned)
    level = manifest.ceiling.step_down_level
    detector = detector_for(manifest.abstention_detector)
    if not scored:
        status = NOT_EXECUTED
    else:
        status = "complete" if validation is not None and validation.ok else "partial"
    return {
        "status": status,
        "manifest": manifest_summary(manifest),
        "step_down": {"level": int(level), "label": STEP_DOWN_LABELS[level]},
        "abstention_detector": {"version": detector.version, "sha256": detector.sha256},
        "journal_validation": validation.summary() if validation else None,
        "cells": cells,
        "seed_summary": _summaries(manifest, scored),
        "macro": _macro(manifest, cells),
        "paired_ag": _paired_ag(manifest, scored),
        "risk_coverage": _risk_coverage(manifest, scored),
        "index_costs": _index_costs(manifest, prepared),
        "comparability_deviations": list(STANDING_DEVIATIONS),
    }
