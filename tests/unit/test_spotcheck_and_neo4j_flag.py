"""Tests for G1→G2 spotcheck modes and run-cases --neo4j flag."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_graphrag.cli import run_cases_main, score_spotcheck_main, spotcheck_main


def _seed_line() -> str:
    return json.dumps(
        {
            "head": {"name": "A", "type": "Company"},
            "relation": "PARENT_OF",
            "tail": {"name": "B", "type": "Company"},
            "confidence": 0.9,
            "source_span": "A parent of B",
            "source_doc_id": "d1",
            "source_chunk_id": "c1",
        },
        ensure_ascii=False,
    )


def test_spotcheck_seed_mode(tmp_path: Path):
    triples = tmp_path / "seed.jsonl"
    triples.write_text(_seed_line() + "\n", encoding="utf-8")
    out = tmp_path / "spot.jsonl"
    spotcheck_main(
        [
            "--triples",
            str(triples),
            "--out",
            str(out),
            "--mode",
            "seed",
            "--schema",
            str(Path(__file__).resolve().parents[2] / "configs/schema/domain_v0.yaml"),
        ]
    )
    rows = [
        json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["human_label"] == "correct"
    assert rows[0]["label_source"] == "seed_baseline_schema_valid"
    summary = json.loads(out.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summary["mode"] == "seed"
    assert summary["correct_rate"] == 1.0


def test_spotcheck_llm_mode_pending(tmp_path: Path):
    triples = tmp_path / "t.jsonl"
    triples.write_text(_seed_line() + "\n", encoding="utf-8")
    out = tmp_path / "spot_llm.jsonl"
    schema = Path(__file__).resolve().parents[2] / "configs/schema/domain_v0.yaml"
    spotcheck_main(
        [
            "--triples",
            str(triples),
            "--out",
            str(out),
            "--mode",
            "llm",
            "--schema",
            str(schema),
        ]
    )
    rows = [
        json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert rows[0]["human_label"] == "pending_human"
    summary = json.loads(out.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summary["pending_human"] == 1
    assert summary["correct_rate"] is None


def test_score_spotcheck_after_labels(tmp_path: Path):
    path = tmp_path / "spot.jsonl"
    path.write_text(
        json.dumps({"human_label": "correct"})
        + "\n"
        + json.dumps({"human_label": "incorrect"})
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as ei:
        score_spotcheck_main(["--in", str(path)])
    assert ei.value.code == 3  # below 70% gate
    summary = json.loads(path.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summary["correct"] == 1
    assert summary["incorrect"] == 1
    assert summary["correct_rate"] == 0.5
    assert summary["pass_g1_extract_gate"] is False


def test_score_spotcheck_pass_gate(tmp_path: Path):
    path = tmp_path / "spot.jsonl"
    lines = [json.dumps({"human_label": "correct"}) for _ in range(7)]
    lines.append(json.dumps({"human_label": "incorrect"}))
    # 7/8 = 87.5%
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    score_spotcheck_main(["--in", str(path)])
    summary = json.loads(path.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summary["pass_g1_extract_gate"] is True


def test_run_cases_rejects_memory_and_neo4j():
    with pytest.raises(SystemExit) as ei:
        run_cases_main(["--no-llm", "--memory-graph", "--neo4j"])
    assert ei.value.code == 2
