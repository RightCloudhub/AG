"""B-07/B-09 contracts: scorer vs hand-computed EM/F1, abstention, recall, ledgers, alignment."""

from __future__ import annotations

from datetime import date

import pytest

from agentic_graphrag.eval.public.accounting import (
    UNKNOWN_USAGE,
    UsageLedger,
    combine,
    ledger_from_budget,
    ledger_from_cost,
    ledger_from_payload,
    money,
    sum_ledgers,
)
from agentic_graphrag.eval.public.adapters_base import classify_error, error_result
from agentic_graphrag.eval.public.alignment import (
    AlignMethod,
    EvidenceRef,
    PassageResolver,
    align,
    relation_key,
)
from agentic_graphrag.eval.public.enums import DatasetName, ErrorKind, TrialStatus
from agentic_graphrag.eval.public.manifest_models import PricePoint
from agentic_graphrag.eval.public.records import Passage, passage_id_for
from agentic_graphrag.eval.public.scoring import (
    answer_span,
    contain_match,
    detector_for,
    exact_match,
    f1_score,
    normalize_answer,
    recall_at,
)


def test_squad_normalization() -> None:
    assert normalize_answer("The  Amber-River!") == "amberriver"
    assert normalize_answer("an apple, a pear") == "apple pear"


@pytest.mark.parametrize(
    ("pred", "golds", "em", "f1"),
    [
        ("Answer: Lisbon", ["Lisbon"], 1.0, 1.0),
        ("the Amber River", ["Amber River"], 1.0, 1.0),
        # pred tokens {amber, river, delta}, gold {amber, river}: P=2/3 R=1 -> F1=0.8
        ("Amber River delta", ["Amber River"], 0.0, 0.8),
        # alias max: second alias matches exactly
        ("Czechia", ["Czech Republic", "Czechia"], 1.0, 1.0),
        ("Porto", ["Lisbon"], 0.0, 0.0),
        ("", ["Lisbon"], 0.0, 0.0),
    ],
)
def test_em_f1_match_hand_computed(pred: str, golds: list[str], em: float, f1: float) -> None:
    assert exact_match(pred, golds) == em
    assert f1_score(pred, golds) == pytest.approx(f1)


def test_answer_span_uses_last_marker_first_line() -> None:
    assert answer_span("Reasoning...\nAnswer: Tomas Berg\nextra") == "Tomas Berg"
    assert answer_span("plain text") == "plain text"
    assert answer_span("Answer:   ") == ""


def test_contain_match_is_named_and_looser() -> None:
    assert contain_match("She was born in Lisbon.", ["Lisbon"]) == 1.0
    assert exact_match("She was born in Lisbon.", ["Lisbon"]) == 0.0


def test_shared_abstention_detector() -> None:
    det = detector_for("abstain-v1")
    assert det.abstained("Answer: cannot answer")
    assert det.abstained("无法基于现有知识回答。原因: x")
    assert det.abstained("There is not enough information.")
    assert det.abstained("   ")
    assert not det.abstained("Answer: Lisbon")
    assert len(det.sha256) == 64
    with pytest.raises(ValueError, match="unknown"):
        detector_for("nope")


def test_recall_at_cutoffs() -> None:
    gold = ["a", "b"]
    assert recall_at(gold, ["x", "a", "y", "b"], 2) == 0.5
    assert recall_at(gold, ["x", "a", "y", "b"]) == 1.0
    assert recall_at([], ["a"]) is None


def test_budget_ledger_is_a_per_query_delta() -> None:
    before = {"llm_calls": 3, "prompt_tokens": 300, "completion_tokens": 30, "embed_calls": 2}
    after = {"llm_calls": 4, "prompt_tokens": 410, "completion_tokens": 45, "embed_calls": 3}
    ledger = ledger_from_budget(before, after, elapsed_ms=12.5)
    assert (ledger.llm_calls, ledger.prompt_tokens, ledger.completion_tokens) == (1, 110, 15)
    assert ledger.embed_calls == 1 and ledger.embed_tokens is None and ledger.total_tokens == 125


def test_unknown_usage_stays_null_with_coverage() -> None:
    known = UsageLedger(prompt_tokens=10, completion_tokens=5, llm_calls=1, elapsed_ms=3)
    assert UNKNOWN_USAGE.total_tokens is None and not UNKNOWN_USAGE.spend_known
    combined = combine([known, UNKNOWN_USAGE])
    assert combined.prompt_tokens is None and combined.llm_calls is None
    total = sum_ledgers([known, UNKNOWN_USAGE])
    assert total.coverage("prompt_tokens") == 0.5 and total.mean("prompt_tokens") == 10
    assert sum_ledgers([]).coverage("llm_calls") is None
    assert ledger_from_payload(None, elapsed_ms=None) == UNKNOWN_USAGE
    assert ledger_from_cost({"llm_calls": 2}, elapsed_ms=1.0).prompt_tokens is None


def test_money_needs_a_dated_price() -> None:
    price = PricePoint(
        model_id="m", prompt_per_million=2.0, completion_per_million=8.0, as_of=date(2026, 11, 1)
    )
    ledger = UsageLedger(prompt_tokens=1_000_000, completion_tokens=500_000, llm_calls=1)
    assert money(ledger, price) == pytest.approx(6.0)
    assert money(ledger, None) is None
    assert money(UNKNOWN_USAGE, price) is None


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (RuntimeError("HTTP 429 Too Many Requests"), ErrorKind.RATE_LIMIT),
        (RuntimeError("403 Forbidden"), ErrorKind.FORBIDDEN),
        (TimeoutError("timed out"), ErrorKind.TIMEOUT),
        (ConnectionError("connection reset"), ErrorKind.TRANSPORT),
        (RuntimeError("budget exhausted"), ErrorKind.BUDGET),
        (ValueError("bad"), ErrorKind.OTHER),
    ],
)
def test_error_classification(exc: BaseException, kind: ErrorKind) -> None:
    assert classify_error(exc) is kind


def test_error_rows_keep_measured_latency_and_known_usage() -> None:
    usage = UsageLedger(prompt_tokens=7, completion_tokens=0, llm_calls=1)
    row = error_result(TimeoutError("timed out"), elapsed_ms=900.0, usage=usage)
    assert row.status is TrialStatus.TIMEOUT and row.usage.elapsed_ms == 900.0
    assert row.usage.prompt_tokens == 7
    assert error_result(ValueError("x"), elapsed_ms=5.0).usage.prompt_tokens is None


def _passage(title: str, text: str) -> Passage:
    ds = DatasetName.HOTPOTQA
    return Passage(passage_id=passage_id_for(ds, title, text), dataset=ds, title=title, text=text)


def test_alignment_methods_and_audit_counts() -> None:
    a = _passage("Maria Duarte", "Maria Duarte is a Portuguese novelist born in Lisbon in 1961.")
    b = _passage("Porto", "Porto is a city in northern Portugal with a famous bridge.")
    rel = relation_key("Maria Duarte", "BORN_IN", "Lisbon")
    resolver = PassageResolver([a, b], relation_sources={rel: (a.passage_id,)})
    refs = [
        EvidenceRef(ref_id=f"chunk:{a.passage_id}"),
        EvidenceRef(ref_id="x", chunk_ids=(b.passage_id,)),
        EvidenceRef(ref_id="nbr:Lisbon:BORN_IN:Maria Duarte:0"),
        EvidenceRef(
            ref_id="y", content="  porto IS a city in northern Portugal with a famous bridge. "
        ),
        EvidenceRef(ref_id="z", content="Duarte is a Portuguese novelist born in Lisbon"),
        EvidenceRef(ref_id="path:a:b:0", content="a -> b"),
    ]
    ranked, report = align(resolver, refs)
    methods = [i.method for i in report.items]
    assert methods[:5] == [
        AlignMethod.ID,
        AlignMethod.CITATION,
        AlignMethod.GRAPH,
        AlignMethod.TEXT_EXACT,
        AlignMethod.FUZZY,
    ]
    assert ranked == (a.passage_id, b.passage_id)
    summary = report.summary()
    assert summary["mapped"] == 5 and summary["missing"] == 1 and summary["fuzzy"] == 1
    assert report.coverage == pytest.approx(5 / 6)


def test_fuzzy_alignment_reports_ambiguity() -> None:
    text = "the same shared sentence about rivers and cities appears twice here"
    a = _passage("A", text)
    b = _passage("B", text + " ")
    resolver = PassageResolver([a, b])
    _, report = align(
        resolver, [EvidenceRef(ref_id="q", content="shared sentence about rivers and cities")]
    )
    assert report.items[0].status == "ambiguous" and len(report.items[0].passage_ids) == 2
