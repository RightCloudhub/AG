"""Attribution priority, reachability and unmatched gold are explicit."""

import pytest

from agentic_graphrag.eval.drift_alignment import align_passages
from agentic_graphrag.eval.search_drift import (
    analyze_row,
    contain_match,
    reachable_unasked,
    summarize,
    summarize_repeats,
)


def row(prediction="wrong", hits=(), **kwargs):
    return {
        "case_id": "q1",
        "prediction": prediction,
        "chain": {"steps": [{"tool_calls": [{"hits": list(hits)}]}]},
        **kwargs,
    }


CASE = {
    "id": "q1",
    "gold_answer": "Bridge",
    "gold_evidence": ["chunk:c1", "c2"],
    "metadata": {
        "aligned_gold_chunks": ["c1", "c2"],
        "gold_evidence_by_hop": {"1": ["c1"], "2": ["c2"]},
    },
}


@pytest.mark.parametrize(
    "record,expected",
    [
        (row("Bridge; cannot determine", llm_eval=False, false_resolution=True), "judge_error"),
        (
            row("cannot determine", hits=["c1", "c2"], llm_eval=False, false_resolution=True),
            "abstention",
        ),
        (row(hits=["c1", "c2"], llm_eval=False), "reasoning_error"),
        (row(hits=["c1"], false_resolution=True), "reasoning_error"),
        (row(hits=["c1"], posthoc_sufficient=False, status="answered"), "reasoning_error"),
        (row(hits=["c1"], llm_eval=False), "evidence_missing"),
        (row("Bridge"), "correct"),
        (row("Equivalent", llm_eval=True), "correct"),
    ],
)
def test_mutually_exclusive_failure_priority(record, expected):
    assert analyze_row(record, CASE)["attribution"] == expected


def test_recall_uses_unused_retrieved_passages_and_reports_hops():
    result = analyze_row(row(hits=["chunk:c1"]), CASE)
    assert result["evidence_recall"] == 0.5
    assert result["by_hop"] == {"1": 1.0, "2": 0.0}
    assert reachable_unasked(row(hits=["c1"]), row(hits=["c1", "c2"]), {"chunk:c1", "c2"}) == ["c2"]


def test_unaligned_gold_is_excluded_and_no_judge_score_is_invented():
    case = {**CASE, "metadata": {"unaligned_gold_passages": [0]}}
    result = analyze_row(row(), case)
    assert result["evidence_recall"] is None
    assert result["attribution"] == "unaligned_or_unlabeled"
    report = summarize([row()], {"q1": case})
    assert report["llm_eval_accuracy"] is None and report["llm_eval_n"] == 0
    assert report["unaligned_n"] == 1
    legacy = analyze_row(row(hits=["Bridge"]), {"gold_evidence": ["Bridge", "PARENT_OF"]})
    assert legacy["unaligned"] and legacy["evidence_recall"] is None


def test_selective_accuracy_and_repeat_variance():
    report = summarize(
        [
            row("Bridge", cost={"tokens": 10, "llm_calls": 1}, hop_count=2),
            row("无法确定", cost={"tokens": 20, "llm_calls": 3}, hop_count=4),
        ],
        {"q1": CASE},
    )
    assert report["contain_match"] == 0.5
    assert report["selective_accuracy"] == 1
    assert report["abstention_rate"] == 0.5
    assert report["mean_tokens"] == 15 and report["mean_rounds"] == 3
    assert summarize_repeats([report, {"contain_match": 1}])["contain_match_variance"] == 0.125
    assert summarize([], {})["contain_match"] is None
    assert not contain_match("anything", "")


def test_alignment_exact_fuzzy_and_unmatched():
    passage = "Bridge headquarters are situated in Santa Monica, California."
    chunks = [
        {"id": "exact", "content": "Prefix " + passage},
        {"id": "fuzzy", "content": passage.replace("headquarters", "headquarterx")},
    ]
    exact = align_passages([passage, "unmatched unrelated evidence"], chunks)
    assert exact["mappings"][0]["chunk_ids"] == ["exact"]
    assert exact["unmatched"] == [1] and exact["coverage"] == 0.5
    fuzzy = align_passages([passage], chunks[1:])
    assert fuzzy["mappings"][0]["method"] == "fuzzy"
    assert align_passages([], [])["coverage"] is None
    with pytest.raises(ValueError):
        align_passages([], [], fingerprint_chars=0)


@pytest.mark.parametrize("key", ["llm_eval", "false_resolution", "posthoc_sufficient"])
def test_labels_cannot_use_truthy_strings(key):
    with pytest.raises(ValueError):
        analyze_row(row(**{key: "false"}), CASE)
