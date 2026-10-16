import json
from pathlib import Path

from agentic_graphrag.eval.report import (
    build_comparison_report,
    evidence_recall_for_row,
    score_system_rows,
    write_comparison_report,
)


def test_score_system_rows_accuracy(tmp_path: Path):
    rows = [
        {"case_id": "a", "gold": "Elena Varga", "prediction": "Elena Varga", "latency_ms": 10},
        {"case_id": "b", "gold": "Helix", "prediction": "wrong", "latency_ms": 20},
    ]
    m = score_system_rows(rows, system="agentic")
    assert m.total == 2
    assert m.correct == 1
    assert m.accuracy == 0.5
    assert m.latency_p50_ms > 0


def test_evidence_recall_from_gold_path():
    cases = {
        "c1": {
            "id": "c1",
            "hops": 2,
            "gold_path": ["NovaTech Industries", "PARENT_OF", "Apex Holdings"],
        }
    }
    row = {
        "case_id": "c1",
        "prediction": "Apex Holdings",
        "explored_paths": ["novatech industries subsidiary_of apex holdings company"],
        "chain": {
            "claims": [{"text": "Apex Holdings", "evidence_ids": ["e1"]}],
            "steps": [{"conclusion": "Apex Holdings -[PARENT_OF]-> NovaTech Industries"}],
        },
    }
    rec = evidence_recall_for_row(row, cases)
    assert rec is not None
    assert rec > 0


def test_build_comparison_report(tmp_path: Path):
    agentic = tmp_path / "agentic.jsonl"
    baseline = tmp_path / "baseline.jsonl"
    cases = tmp_path / "cases.jsonl"
    agentic.write_text(
        json.dumps(
            {
                "case_id": "poc-2hop-01",
                "gold": "Elena Varga",
                "prediction": "Elena Varga",
                "latency_ms": 5,
                "status": "answered",
                "cost": {"tokens": 0, "llm_calls": 0, "latency_ms": 5},
                "chain": {
                    "claims": [{"text": "Elena Varga", "evidence_ids": ["e1"]}],
                    "explored_paths": ["elena varga ceo_of apex holdings"],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    baseline.write_text(
        json.dumps(
            {
                "case_id": "poc-2hop-01",
                "gold": "Elena Varga",
                "prediction": "Based on evidence Apex and Elena",
                "latency_ms": 1,
                "status": "partial",
                "cost": {"tokens": 0, "llm_calls": 0, "latency_ms": 1},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cases.write_text(
        json.dumps(
            {
                "id": "poc-2hop-01",
                "hops": 2,
                "gold_answer": "Elena Varga",
                "gold_path": ["NovaTech", "PARENT_OF", "Apex", "CEO_OF", "Elena Varga"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = build_comparison_report(agentic_path=agentic, baseline_path=baseline, cases_path=cases)
    assert "summary" in report
    assert report["systems"]["agentic"]["accuracy"] == 1.0
    paths = write_comparison_report(report, tmp_path / "out")
    assert paths["json"].exists()
    assert paths["md"].exists()
