"""P2-EV-01: case schema, stratification, deterministic gold generator."""

from pathlib import Path

from agentic_graphrag.config import resolve_path
from agentic_graphrag.eval.cases import (
    CaseCategory,
    EvalCase,
    StratificationSpec,
    dump_cases,
    load_cases,
    validate_stratification,
)
from agentic_graphrag.eval.gold_gen import generate_gold_cases
from agentic_graphrag.knowledge.schema_check import Triple


def test_eval_case_resolved_category():
    c = EvalCase(id="x", question="q?", gold_answer="Elena", hops=2)
    assert c.resolved_category() == CaseCategory.HOP2
    na = EvalCase(id="y", question="q?", gold_answer="no answer", hops=0)
    assert na.resolved_category() == CaseCategory.NO_ANSWER


def test_load_poc_cases_soft_stratification():
    cases = load_cases(resolve_path("evals/datasets/poc_cases.jsonl"))
    assert len(cases) >= 10
    report = validate_stratification(cases, strict_total=False)
    assert report.total == len(cases)
    # POC set is small — must not error on total when soft
    assert report.ok or not any("min_total" in e for e in report.errors)


def test_strict_stratification_fails_small_set():
    cases = [
        EvalCase(id="a", question="q", gold_answer="x", hops=2, category=CaseCategory.HOP2)
    ]
    report = validate_stratification(cases, StratificationSpec(min_total=200), strict_total=True)
    assert not report.ok
    assert any("min_total" in e for e in report.errors)


def test_gold_gen_from_seed_triples():
    path = resolve_path("data/processed/seed_triples.jsonl")
    triples = [
        Triple.model_validate(__import__("json").loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    cases = generate_gold_cases(
        triples, max_2hop=20, max_3hop=10, max_open=5, max_no_answer=20
    )
    cats = {}
    for c in cases:
        cats[c.resolved_category().value] = cats.get(c.resolved_category().value, 0) + 1
    assert cats.get("2hop", 0) >= 1
    assert cats.get("no_answer", 0) >= 20
    # all questions unique
    assert len({c.question for c in cases}) == len(cases)


def test_dump_and_reload(tmp_path: Path):
    cases = [
        EvalCase(
            id="t1",
            question="Who?",
            gold_answer="Elena",
            hops=2,
            category=CaseCategory.HOP2,
            gold_path=["A", "CEO_OF", "B"],
        )
    ]
    out = dump_cases(cases, tmp_path / "c.jsonl")
    loaded = load_cases(out)
    assert loaded[0].id == "t1"
    assert loaded[0].gold_path[1] == "CEO_OF"
