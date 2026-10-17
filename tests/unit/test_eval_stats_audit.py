"""Eval statistics, paired comparison and gold audit (ARCH_OPTIMIZATION_V2 O-1/O-2)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from agentic_graphrag.eval.compare import compare_runs, correctness, format_text, run_summary
from agentic_graphrag.eval.gold_audit import TripleIndex, audit_case, audit_cases, gold_answers
from agentic_graphrag.eval.stats import (
    breakdown,
    category_of,
    mcnemar,
    percentile,
    wilson_interval,
)

_ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_wilson_matches_reference_values() -> None:
    lo, hi = wilson_interval(21, 31)
    assert lo == pytest.approx(0.501, abs=1e-3)
    assert hi == pytest.approx(0.814, abs=1e-3)
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_mcnemar_exact() -> None:
    a = {str(i): True for i in range(10)}
    b = {str(i): i >= 8 for i in range(10)}  # A wins 8 discordant pairs
    res = mcnemar(a, b)
    assert (res.a_only, res.b_only, res.both) == (8, 0, 2)
    assert res.p_value == pytest.approx(2 / 256)
    assert mcnemar(a, a).p_value == 1.0


def test_breakdown_and_percentile() -> None:
    cases = {"x": {"category": "2hop"}, "y": {"category": "3hop"}, "z": {"category": "2hop"}}
    groups = breakdown({"x": True, "y": False, "z": False}, cases, category_of)
    assert groups["2hop"].correct == 1 and groups["2hop"].total == 2
    assert groups["3hop"].to_dict()["accuracy"] == 0.0
    assert percentile([1, 2, 3, 4], 50) == 2.0
    assert percentile([], 95) == 0.0


def _row(cid: str, pred: str, gold: str = "Elena Varga", **kw) -> dict:
    return {"case_id": cid, "prediction": pred, "gold": gold, "latency_ms": 10, **kw}


def test_compare_runs_verdicts() -> None:
    cases = {f"c{i}": {"id": f"c{i}", "category": "2hop"} for i in range(8)}
    base = [_row(f"c{i}", "nope") for i in range(8)]
    cand = [_row(f"c{i}", "Elena Varga") for i in range(8)]
    report = compare_runs(base, cand, cases)
    assert report["verdict"] == "better"
    assert report["paired"]["b_only"] == 8
    assert "verdict   better" in format_text(report)
    assert compare_runs(cand, base, cases)["verdict"] == "worse"
    assert compare_runs(base, base, cases)["verdict"] == "no_significant_difference"


def test_correctness_treats_errors_as_wrong() -> None:
    rows = [_row("a", "", error="boom"), _row("b", "Elena Varga")]
    assert correctness(rows) == {"a": False, "b": True}
    summary = run_summary(rows, {})
    assert summary["correct"] == 1 and summary["by_category"]["unknown"]["total"] == 2


def _index() -> TripleIndex:
    idx = TripleIndex()
    idx.add("Apex", "PARENT_OF", "NovaTech")
    idx.add("Elena", "CEO_OF", "Apex")
    idx.add("A", "COMPETES_WITH", "B")
    idx.add("A", "COMPETES_WITH", "C")
    return idx


def test_audit_clean_case_and_conventions() -> None:
    case = {
        "id": "ok",
        "gold_answer": "Elena",
        "gold_path": ["NovaTech", "SUBSIDIARY_OF", "Apex", "CEO_OF", "Elena"],
    }
    audit = audit_case(case, _index())
    assert audit.issues == []  # backwards CEO_OF is the documented convention
    assert [n.lower() for n in audit.reachable] == ["elena"]


def test_audit_flags_inverse_label_ambiguity_and_missing() -> None:
    idx = _index()
    inv = audit_case(
        {"id": "i", "gold_answer": "Elena", "gold_path": ["NovaTech", "PARENT_OF", "Apex"]}, idx
    )
    assert inv.issues[0].startswith("inverse_label")
    amb = audit_case({"id": "a", "gold_answer": "B", "gold_path": ["A", "COMPETES_WITH", "B"]}, idx)
    assert amb.issues == ["ambiguous_gold"]
    miss = audit_case({"id": "m", "gold_answer": "Z", "gold_path": ["A", "SUPPLIES", "Z"]}, idx)
    assert any(i.startswith("path_edge_missing") for i in miss.issues)
    assert "gold_unreachable" in miss.issues


def test_audit_alternatives_open_and_report() -> None:
    idx = _index()
    alt = {
        "id": "alt",
        "gold_answer": "Elena",
        "gold_path": ["NovaTech", "PARENT_OF/SUBSIDIARY_OF", "Apex", "CEO_OF", "Elena"],
    }
    assert audit_case(alt, idx).issues == []
    open_case = {"id": "o", "category": "open", "gold_path": ["A", "COMPETES_WITH", "B"]}
    assert audit_case(open_case, idx).issues == []
    report = audit_cases([alt, open_case, {"id": "short", "gold_path": []}], idx)
    assert report["total"] == 3 and report["flagged"] == 0
    assert gold_answers("B and C, D") == {"b", "c", "d"}


def test_scripts_end_to_end(tmp_path: Path, capsys) -> None:
    triples = tmp_path / "t.jsonl"
    triples.write_text(
        json.dumps({"head": {"name": "Elena"}, "relation": "CEO_OF", "tail": {"name": "Apex"}})
        + "\n",
        encoding="utf-8",
    )
    cases = tmp_path / "c.jsonl"
    case = {"id": "c1", "gold_answer": "Elena", "gold_path": ["Apex", "CEO_OF", "Elena"]}
    cases.write_text(json.dumps(case) + "\n", encoding="utf-8")
    out = tmp_path / "audit.json"
    assert (
        _load_script("audit_gold").main([str(cases), "--triples", str(triples), "--json", str(out)])
        == 0
    )
    assert json.loads(out.read_text())["flagged"] == 0

    run = tmp_path / "run.jsonl"
    run.write_text(json.dumps(_row("c1", "Elena")) + "\n", encoding="utf-8")
    cmp_out = tmp_path / "cmp.json"
    rc = _load_script("eval_compare").main(
        [str(run), str(run), "--cases", str(cases), "--json", str(cmp_out), "--fail-on-worse"]
    )
    assert rc == 0
    assert "no_significant_difference" in capsys.readouterr().out
