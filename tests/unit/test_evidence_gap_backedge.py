"""Refactor §1.5 / §6.2: evidence gaps go back to retrieval; phrasing is repaired in place."""

from __future__ import annotations

from types import SimpleNamespace

from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import QueryStatus, ReasoningChain
from agentic_graphrag.generation import answer as ans
from agentic_graphrag.generation.verdict import (
    VerdictKind,
    classify_rejection,
    evidence_gap,
)


def test_classify_rejection() -> None:
    assert classify_rejection("") is VerdictKind.OK
    assert classify_rejection("claim missing evidence_ids") is VerdictKind.PHRASING
    assert (
        classify_rejection("claim text not supported by cited evidence content")
        is VerdictKind.EVIDENCE
    )


def _patch_llm(monkeypatch, reason: str, missing: list[str]) -> list[str]:
    calls: list[str] = []
    payload = ans.AnswerPayload(answer="x", status=QueryStatus.ANSWERED, missing_info=missing)

    def fake_llm_answer(chain, evidence, llm, **kw):
        calls.append(kw.get("repair_reason", ""))
        return payload

    monkeypatch.setattr(ans, "_llm_answer", fake_llm_answer)
    monkeypatch.setattr(ans, "_apply_payload", lambda *a, **k: (None, reason))
    return calls


def test_evidence_rejection_reported_not_regenerated(monkeypatch) -> None:
    calls = _patch_llm(
        monkeypatch, "claim text not supported by cited evidence content", ["Who owns Acme?"]
    )
    chain = ReasoningChain(question="Q?", route="agentic")
    ev = [Candidate(id="c1", source="vector", content="t")]
    out = ans._generate_with_llm(
        chain,
        ev,
        SimpleNamespace(),
        conclusions="",
        guardrail_status="ok",
        tier=ans.Tier.STRONG,
        report_evidence_gaps=True,
    )
    gap = evidence_gap(out)
    assert gap is not None and gap.missing == ["Who owns Acme?"]
    assert calls == [""]  # no regenerate call


def test_phrasing_rejection_still_regenerates(monkeypatch) -> None:
    calls = _patch_llm(monkeypatch, "claim missing evidence_ids", ["Who owns Acme?"])
    chain = ReasoningChain(question="Q?", route="agentic")
    ev = [Candidate(id="c1", source="vector", content="t")]
    ans._generate_with_llm(
        chain,
        ev,
        SimpleNamespace(),
        conclusions="",
        guardrail_status="ok",
        tier=ans.Tier.STRONG,
        report_evidence_gaps=True,
    )
    assert calls == ["", "claim missing evidence_ids"]


def test_runtime_loops_back_with_new_subquestions() -> None:
    from agentic_graphrag.agent.guardrails import GuardrailConfig
    from agentic_graphrag.agent.loop_runtime import AgentRuntime

    rt = AgentRuntime.__new__(AgentRuntime)
    rt.guard_cfg = GuardrailConfig(max_hops=4)
    rt.guards = SimpleNamespace(state=SimpleNamespace(tripped=False))
    rt.memory = SimpleNamespace(to_snapshot=lambda: {})
    state = {"hop": 1, "sub_questions": [{"id": "sq0", "text": "a"}], "answer_retries": 0}
    assert rt._can_retrieve_again(state)
    chain = ReasoningChain(question="Q?", route="agentic")
    out = rt._loop_back_for_gap(state, chain, ["Who owns Acme?"])
    assert out["current_index"] == 1 and out["sub_questions"][1]["text"] == "Who owns Acme?"
    assert rt.route_after_answer(out) == "executor"
    assert not rt._can_retrieve_again(out)  # one back-edge per run
    assert rt.route_after_answer({"done": True}) == "end"
