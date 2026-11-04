"""Planning and judge fallbacks remain visible even when generation succeeds."""

from collections.abc import Iterator
from unittest.mock import Mock

import pytest

from agentic_graphrag.agent.executor_plan import _try_llm_plan
from agentic_graphrag.app.contracts import QueryRequest
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.core.enums import StreamEvent
from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.reasoning import Claim, QueryStatus, ReasoningChain
from agentic_graphrag.generation.answer import AnswerPayload, generate_answer
from agentic_graphrag.generation.entailment import JUDGE_ABSTAINED
from agentic_graphrag.llm.provider import MockLLMProvider

QUESTION = "Explain this topic in detail."
EVIDENCE = Candidate(
    id="fact",
    source=CandidateSource.FULLTEXT_CHUNK,
    content="Elena Varga is the CEO of Apex Holdings.",
    score=1.0,
)
ANSWER = AnswerPayload(
    answer="Elena Varga is the CEO of Apex Holdings.",
    status=QueryStatus.ANSWERED,
    claims=[Claim(text="Elena Varga is the CEO of Apex Holdings.", evidence_ids=["fact"])],
)


@pytest.fixture
def service(monkeypatch) -> Iterator[QueryService]:
    from agentic_graphrag.app import query_run, wiring
    from agentic_graphrag.generation import answer, entailment

    svc = QueryService.create_offline()
    svc.allow_llm = True
    llm = MockLLMProvider()
    monkeypatch.setattr(wiring, "_embed_llm", lambda **kwargs: llm)
    monkeypatch.setattr(query_run, "build_llm_for_service", lambda **kwargs: llm)
    monkeypatch.setattr(answer, "_llm_answer", Mock(return_value=ANSWER))
    monkeypatch.setattr(entailment, "entailment_enabled", lambda llm: True)
    monkeypatch.setattr(entailment, "_ask", Mock(return_value=entailment.VerdictPayload()))
    try:
        yield svc
    finally:
        svc.close()


@pytest.mark.parametrize("failure", ["planner", "triage", "entailment"])
@pytest.mark.parametrize("stream", [False, True])
def test_successful_answer_reports_fallback_and_is_not_cached(
    service, monkeypatch, failure, stream
):
    from agentic_graphrag.agent import executor_plan, pipeline
    from agentic_graphrag.agent import triage as triage_module
    from agentic_graphrag.generation import entailment

    broken = Mock(side_effect=ConnectionError("light model unavailable"))
    if failure == "planner":
        monkeypatch.setattr(executor_plan, "complete_structured", broken)
    elif failure == "triage":
        monkeypatch.setattr(triage_module, "complete_structured", broken)
    else:
        monkeypatch.setattr(entailment, "_ask", broken)

    def agentic(ctx, *, seed_evidence=None):
        if failure == "planner":
            assert _try_llm_plan(ctx.executor, QUESTION, []) is None
        chain = generate_answer(ReasoningChain(question=QUESTION), [EVIDENCE], ctx.llm)
        yield StreamEvent.FINAL_CHAIN, chain

    monkeypatch.setattr(pipeline, "_iter_agentic", agentic)
    request = QueryRequest(question=QUESTION)
    if stream:
        events = list(service.stream_query_events(request, tenant_id="alpha", user_id="reader"))
        data = dict(events)[StreamEvent.ANSWER]
        assert StreamEvent.DEGRADED in dict(events)
        assert dict(events)[StreamEvent.DEGRADED]["channels"][0]["channel"] == "llm"
    else:
        data = service.run_query(request, tenant_id="alpha", user_id="reader").model_dump()
    assert broken.call_count > 0
    assert data["status"] == QueryStatus.ANSWERED
    assert data["answer"] == ANSWER.answer
    assert data["metadata"]["degraded_channels"] == [
        {"channel": "llm", "reason": "llm_unavailable", "status": "degraded"}
    ]
    assert not data["metadata"].get("llm_degraded")
    if failure == "entailment":
        assert data["metadata"][JUDGE_ABSTAINED] == "ConnectionError"
    assert service.retrieval_cache.answers.stats()["size"] == 0
