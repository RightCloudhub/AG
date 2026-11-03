"""Dataset provenance, complete-group scoring and offline CLI contracts."""

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentic_graphrag.cli.paraphrase import paraphrase_main
from agentic_graphrag.eval.cases import EvalCase, dump_cases, load_cases
from agentic_graphrag.eval.paraphrase import VARIANTS, group_cases, summarize

ROOT = Path(__file__).resolve().parents[2]


def _cases(gold="Alice"):
    return [
        EvalCase(
            id=f"q::{tag}",
            question="Who leads Acme?",
            gold_answer=gold,
            metadata={"original_id": "q", "variant": tag},
        )
        for tag in VARIANTS
    ]


def _rows(cases, prediction="Alice"):
    return [{"case_id": c.id, "prediction": prediction, "status": "ok"} for c in cases]


def test_dataset_inherits_exact_dev_labels_and_has_five_distinct_variants():
    source = {c.id: c for c in load_cases(ROOT / "evals/datasets/g2_dev.jsonl")}
    groups = group_cases(load_cases(ROOT / "evals/datasets/paraphrase.jsonl"))
    assert len(groups) == 40
    assert Counter(forms["original"].hops for forms in groups.values()) == {1: 8, 2: 16, 3: 16}
    for ident, forms in groups.items():
        base = forms["original"]
        assert base.question == source[ident].question
        assert base.gold_answer == source[ident].gold_answer
        assert base.gold_path == source[ident].gold_path
        assert base.gold_evidence == source[ident].gold_evidence
        assert base.hops == source[ident].hops
        assert len({c.question for c in forms.values()}) == 6
        assert all(c.metadata["split"] == "dev" for c in forms.values())


def test_four_hop_supplement_is_separate_and_has_unique_graph_verified_paths():
    triples = [
        json.loads(line)
        for line in (ROOT / "data/processed/pilot_triples.jsonl").read_text().splitlines()
    ]
    edges = {(r["head"]["name"], r["relation"], r["tail"]["name"]) for r in triples}
    groups = group_cases(load_cases(ROOT / "evals/datasets/paraphrase_4hop.jsonl"))
    assert len(groups) == 2
    for forms in groups.values():
        base = forms["original"]
        assert base.hops == 4
        assert base.metadata["split"] == "dev_supplement"
        assert len(base.gold_path) == 9
        for start in range(0, 8, 2):
            anchor, relation, answer = base.gold_path[start : start + 3]
            assert {h for h, r, t in edges if r == relation and t == anchor} == {answer}
        assert base.gold_answer == base.gold_path[-1]


def test_consensus_accepts_same_correct_entity_with_different_wording():
    cases = _cases()
    rows = _rows(cases)
    rows[1]["prediction"] = "The leader of Acme is Alice."
    result = summarize(cases, rows, known_entities=["Alice", "Acme"])
    assert result["consistency"] == 1
    assert result["by_variant"]["synonym"]["accuracy"] == 1


def test_consensus_rejects_all_wrong_stuffing_missing_and_error_answers():
    cases = _cases()
    names = ["Alice", "Bob", "Acme"]
    for prediction in ("Bob", "Alice and Bob", ""):
        assert summarize(cases, _rows(cases, prediction), known_entities=names)["consistency"] == 0
    rows = _rows(cases)
    rows[1]["error"] = "service unavailable"
    result = summarize(cases, rows[:-1], known_entities=names)
    assert result["consistency"] == 0
    assert result["by_variant"]["synonym"]["errors"] == 1
    assert result["by_variant"]["casual"]["total"] == 1
    assert result["by_variant"]["casual"]["accuracy"] == 0


def test_non_entity_consensus_needs_identical_normalized_correct_answers():
    cases = _cases("Acme COMPETES_WITH Beta")
    rows = _rows(cases, "Acme COMPETES_WITH Beta")
    assert summarize(cases, rows, known_entities=[])["consistency"] == 1
    rows[1]["prediction"] += " and Gamma"
    result = summarize(cases, rows, known_entities=[])
    assert result["all_correct_groups"] == 1  # Permissive scorer alone is insufficient.
    assert result["consistency"] == 0
    wrong_relation = _rows(cases, "Acme SUPPLIES Beta")
    assert summarize(cases, wrong_relation, known_entities=[])["consistency"] == 0


def test_group_and_prediction_validation_rejects_inflated_denominators():
    cases = _cases()
    for invalid in ([], cases[:-1], cases + cases[:1]):
        with pytest.raises(ValueError):
            group_cases(invalid)
    changed = cases[0].model_copy(update={"gold_answer": "Bob"})
    with pytest.raises(ValueError, match="Changed gold"):
        group_cases([changed, *cases[1:]])
    invalid_id = cases[0].model_copy(update={"id": "bad"})
    with pytest.raises(ValueError, match="identity"):
        group_cases([invalid_id, *cases[1:]])
    for rows in (_rows(cases) + _rows(cases[:1]), [{"case_id": "unknown"}]):
        with pytest.raises(ValueError, match="prediction IDs"):
            summarize(cases, rows, known_entities=[])


def test_cli_is_offline_by_default_and_closes_app(tmp_path, monkeypatch):
    from agentic_graphrag.cli import paraphrase as cli

    dataset = dump_cases(_cases(), tmp_path / "cases.jsonl")
    triples = tmp_path / "triples.jsonl"
    triples.write_text("")
    calls = []
    app = SimpleNamespace(allow_llm=False, close=lambda: calls.append("closed"))
    monkeypatch.setattr(cli, "build_eval_app", lambda **kw: (calls.append(kw), app)[1])
    monkeypatch.setattr(
        cli,
        "run_case_row_via_app",
        lambda case, **kw: {"case_id": case["id"], "prediction": "Alice", "status": "ok"},
    )
    args = ["--cases", str(dataset), "--seed-triples", str(triples), "--out", str(tmp_path)]
    paraphrase_main(args)
    assert calls[0]["no_llm"] is True
    assert calls[0]["enable_triage"] is True
    assert calls[-1] == "closed"
    result = json.loads((tmp_path / "paraphrase_offline_summary.json").read_text())
    assert result["mode"] == "offline"
    assert result["total"] == 6
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        paraphrase_main([*args, "--live"])
    assert calls[-1] == "closed"


@pytest.mark.parametrize("args", [["--limit-groups", "0"], ["--run-name", "../bad"]])
def test_cli_rejects_invalid_limits_and_output_names(args):
    with pytest.raises(SystemExit):
        paraphrase_main(args)
