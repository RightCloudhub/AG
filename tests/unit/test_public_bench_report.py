"""B-09/B-10/B-00/B-12 contracts: stats on synthetic paired data, reports, decisions."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_graphrag.eval.public import NOT_EXECUTED
from agentic_graphrag.eval.public.enums import ArmName, DatasetName, StepDownLevel, TrialStatus
from agentic_graphrag.eval.public.estimate import estimate_run, units_from_means
from agentic_graphrag.eval.public.journal_check import JournalValidation
from agentic_graphrag.eval.public.manifest import RunManifest, load_manifest
from agentic_graphrag.eval.public.manifest_models import PromotionBounds
from agentic_graphrag.eval.public.planning import plan_trials
from agentic_graphrag.eval.public.promotion import promotion_report
from agentic_graphrag.eval.public.report import build_report
from agentic_graphrag.eval.public.report_md import render_markdown
from agentic_graphrag.eval.public.spike import spike_report
from agentic_graphrag.eval.public.stats_public import (
    cluster_bootstrap_ci,
    describe,
    mcnemar_by_seed,
    paired_bootstrap,
    percentile,
    risk_coverage,
)
from agentic_graphrag.eval.public.trial_scores import ScoredTrial

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "public_bench" / "manifest_fixture.yaml"
QIDS = {ds: [f"{ds.value}-{i}" for i in range(4)] for ds in DatasetName}


def _row(
    arm: ArmName, ds: DatasetName, seed: int, qid: str, *, em: float, **kw: object
) -> ScoredTrial:
    base: dict[str, object] = {
        "arm": arm,
        "dataset": ds,
        "run_seed": seed,
        "question_id": qid,
        "status": TrialStatus.ANSWERED,
        "em": em,
        "f1": em,
        "contain": em,
        "abstained": False,
        "recall": {
            "retrieved@2": 0.5,
            "retrieved@5": 1.0,
            "retrieved_all": 1.0,
            "context": 1.0,
            "cited": None,
        },
        "latency_ms": 100.0,
        "tokens": 1000,
        "llm_calls": 4,
        "attempts": 1,
        "interrupted_attempts": 0,
        "alignment_coverage": {"retrieved": 1.0},
    }
    base.update(kw)
    return ScoredTrial.model_validate(base)


def test_describe_uses_sample_variance() -> None:
    d = describe([1.0, 2.0, 3.0, 4.0])
    assert d["mean"] == 2.5 and d["variance"] == pytest.approx(5 / 3)
    assert describe([2.0])["variance"] is None and describe([])["mean"] is None
    assert percentile([], 95) is None and percentile([1, 2, 3, 4], 50) == 2


def test_cluster_bootstrap_averages_seeds_per_question() -> None:
    values = {("q1", 1): 1.0, ("q1", 2): 0.0, ("q2", 1): 1.0, ("q2", 2): 1.0}
    out = cluster_bootstrap_ci(values, resamples=200, seed=3)
    assert out is not None and out["clusters"] == 2 and out["mean"] == pytest.approx(0.75)
    lo, hi = out["ci"]
    assert 0.5 <= lo <= hi <= 1.0
    assert cluster_bootstrap_ci({}, resamples=10, seed=0) is None
    assert out == cluster_bootstrap_ci(values, resamples=200, seed=3)  # seeded, reproducible


def test_paired_bootstrap_and_mcnemar_on_synthetic_pairs() -> None:
    qs = [f"q{i}" for i in range(30)]
    a = {(q, s): 0.0 for q in qs for s in (1, 2)}
    b = {(q, s): 1.0 if i < 20 else 0.0 for i, q in enumerate(qs) for s in (1, 2)}
    diff = paired_bootstrap(a, b, resamples=300, seed=1)
    assert diff is not None and diff["mean"] == pytest.approx(20 / 30) and diff["excludes_zero"]
    per_seed = mcnemar_by_seed({k: v > 0 for k, v in a.items()}, {k: v > 0 for k, v in b.items()})
    assert set(per_seed) == {1, 2}
    assert per_seed[1]["b_only"] == 20 and per_seed[1]["a_only"] == 0
    assert per_seed[1]["p_value"] < 0.001


def test_risk_coverage_curve() -> None:
    curve = risk_coverage([(0.9, True), (0.5, False), (0.7, True)])
    assert [c["coverage"] for c in curve] == pytest.approx([1 / 3, 2 / 3, 1.0])
    assert curve[-1]["risk"] == pytest.approx(1 / 3)


def _manifest() -> RunManifest:
    return load_manifest(FIXTURE)


def test_report_prints_not_executed_without_results() -> None:
    m = _manifest()
    planned = plan_trials(m, QIDS)
    report = build_report(m, [], planned=planned)
    assert report["status"] == NOT_EXECUTED
    assert all(c["status"] == NOT_EXECUTED for c in report["cells"])
    assert all(p["status"] == NOT_EXECUTED for p in report["paired_ag"])
    assert report["step_down"]["level"] == 0
    md = render_markdown(report)
    assert "**Status: Not Executed**" in md and "UNVERIFIED" in md
    assert md.count("Not Executed") > len(planned) // 4
    assert "scale: full scale" in md


def _ag_rows(n: int = 4) -> list[ScoredTrial]:
    rows: list[ScoredTrial] = []
    ds = DatasetName.HOTPOTQA
    for seed in (11, 22, 33):
        for i in range(n):
            qid = f"{ds.value}-{i}"
            rows.append(_row(ArmName.AG_SINGLE_TIER, ds, seed, qid, em=1.0 if i < n // 4 else 0.0))
            rows.append(
                _row(ArmName.AG_REGISTRY, ds, seed, qid, em=1.0 if i < 3 * n // 4 else 0.0,
                     tokens=1500, confidence=0.9 - i / (10 * n))
            )  # fmt: skip
    rows.append(
        _row(ArmName.PURE_VECTOR, ds, 11, QIDS[ds][0], em=0.0, status=TrialStatus.TIMEOUT,
             abstained=True, latency_ms=None, tokens=None)
    )  # fmt: skip
    return rows


def test_report_with_partial_results_and_paired_ag() -> None:
    m = _manifest()
    planned = plan_trials(m, QIDS)
    rows = _ag_rows()
    validation = JournalValidation(expected=len(planned), completed=len(rows), missing=["x"])
    report = build_report(m, rows, planned=planned, validation=validation)
    assert report["status"] == "partial"
    cell = next(
        c for c in report["cells"]
        if c["arm"] == "ag_registry" and c["dataset"] == "hotpotqa" and c["seed"] == 11
    )  # fmt: skip
    assert cell["status"] == "complete" and cell["em_squad"] == pytest.approx(0.75)
    assert cell["selective_coverage"] == 1.0
    vec = next(
        c
        for c in report["cells"]
        if c["arm"] == "pure_vector" and c["seed"] == 11 and c["dataset"] == "hotpotqa"
    )
    assert vec["status"] == "partial 1/4" and vec["cost"]["timeout_rate"] == 1.0
    assert (
        vec["selective_em"] is None and vec["selective_coverage"] == 0.0
    )  # zero coverage → unknown
    assert vec["cost"]["latency_p95_ms"] is None
    paired = next(p for p in report["paired_ag"] if p["dataset"] == "hotpotqa")
    assert paired["status"] == "executed" and set(paired["mcnemar_em_by_seed"]) == {11, 22, 33}
    assert paired["em_diff"]["mean"] == pytest.approx(0.5)
    assert report["risk_coverage"]["ag_registry"]["score_coverage"] == 1.0
    assert report["risk_coverage"]["ag_single_tier"].startswith("not available")
    assert report["risk_coverage"]["lightrag"] == NOT_EXECUTED
    macro = next(x for x in report["macro"] if x["arm"] == "ag_registry")
    assert macro["em_squad"] == NOT_EXECUTED  # other datasets missing → no macro claim
    md = render_markdown(report)
    assert "McNemar" in md and "seed 11" in md


def test_promotion_not_executed_until_bounds_declared() -> None:
    m = _manifest()
    report = build_report(m, _ag_rows(), planned=plan_trials(m, QIDS))
    out = promotion_report(m, report)
    assert out["decision"] == NOT_EXECUTED and "predeclared" in out["reason"]


def test_promotion_go_and_no_go_from_predeclared_bounds() -> None:
    from datetime import date

    m = _manifest()
    single = m.model_copy(update={"datasets": (m.datasets[0],)})
    bounds = PromotionBounds(
        min_accuracy_gain_pp=10.0,
        max_coverage_drop_pp=5.0,
        max_selective_risk_increase_pp=5.0,
        max_token_ratio=2.0,
        declared_on=date(2026, 11, 4),
        declared_from="pilot",
    )
    go_m = single.model_copy(update={"promotion": bounds})
    qids = {DatasetName.HOTPOTQA: [f"hotpotqa-{i}" for i in range(40)]}
    small = promotion_report(go_m, build_report(go_m, _ag_rows(), planned=plan_trials(go_m, QIDS)))
    assert small["decision"] == "no-go"  # 4 questions: the gain is not significant
    assert small["regressions"] == ["hotpotqa: significant_gain"]
    report = build_report(go_m, _ag_rows(40), planned=plan_trials(go_m, qids))
    out = promotion_report(go_m, report)
    assert out["decision"] == "go", out
    strict = go_m.model_copy(
        update={"promotion": bounds.model_copy(update={"max_token_ratio": 1.2})}
    )
    no_go = promotion_report(
        strict, build_report(strict, _ag_rows(40), planned=plan_trials(strict, qids))
    )
    assert no_go["decision"] == "no-go" and "hotpotqa: token_ratio" in no_go["regressions"]


def test_spike_report_not_executed_then_decision_suggestion() -> None:
    m = load_manifest(ROOT / "configs" / "benchmarks" / "spike_b00.yaml")
    empty = spike_report(m, [], indexing={})
    assert empty["status"] == NOT_EXECUTED and empty["decision"] == NOT_EXECUTED
    rows = [
        _row(arm, ds, 101, f"q{i}", em=em)
        for ds in (DatasetName.HOTPOTQA, DatasetName.MUSIQUE)
        for i in range(3)
        for arm, em in ((ArmName.AG_SINGLE_TIER, 1.0), (ArmName.PURE_VECTOR, 0.0))
    ]
    good = {"status": "executed", "schema_rejection_rate": 0.1, "graph_coverage": 0.9}
    out = spike_report(m, rows, indexing={DatasetName.HOTPOTQA: good, DatasetName.MUSIQUE: good})
    assert (
        out["suggested_decision"] == "continue" and out["decision"] == "pending operator decision"
    )
    bad = {**good, "graph_coverage": 0.2}
    out = spike_report(
        m,
        rows,
        indexing={DatasetName.HOTPOTQA: good, DatasetName.MUSIQUE: bad},
        decision="revise_schema",
    )
    assert out["suggested_decision"] == "revise_schema" and out["decision"] == "revise_schema"
    with pytest.raises(ValueError, match="decision"):
        spike_report(m, rows, indexing={}, decision="ship it")


def test_estimate_applies_step_down_order() -> None:
    m = load_manifest(ROOT / "configs" / "benchmarks" / "hipporag.yaml")
    q = {arm.name: 1000.0 for arm in m.arms}
    idx = {arm.name: 200.0 for arm in m.arms}
    units = units_from_means(q, idx)
    sizes = {
        "questions": {d.name: 1000 for d in m.datasets},
        "passages": {d.name: 10_000 for d in m.datasets},
    }
    undeclared = estimate_run(m, units, **sizes)
    assert (
        undeclared["chosen_level"] == NOT_EXECUTED
        and undeclared["reason"] == "ceiling not declared"
    )
    full = undeclared["levels"][0]
    assert full["trials"] == 54_000  # 3 datasets x 1000 x 6 arms x 3 seeds
    assert [lv["level"] for lv in undeclared["levels"]] == [int(x) for x in StepDownLevel]
    totals = [lv["total_tokens"] for lv in undeclared["levels"]]
    assert totals == sorted(totals, reverse=True)
    level2_budget = int(undeclared["levels"][2]["total_tokens"]) + 1
    capped = m.model_copy(
        update={"ceiling": m.ceiling.model_copy(update={"provider_tokens": level2_budget})}
    )
    assert estimate_run(capped, units, **sizes)["chosen_level"] == 2
    tiny = m.model_copy(update={"ceiling": m.ceiling.model_copy(update={"provider_tokens": 1})})
    assert "every level" in estimate_run(tiny, units, **sizes)["reason"]
    unmeasured = units_from_means({**q, ArmName.LIGHTRAG: None}, idx)
    assert estimate_run(capped, unmeasured, **sizes)["chosen_level"] == NOT_EXECUTED
