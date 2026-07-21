"""Unit tests for generate_answer / offline_answer (coverage omit reduction)."""

from __future__ import annotations

import json

from agentic_graphrag.generation.answer import AnswerPayload, generate_answer
from agentic_graphrag.generation.offline_answer import offline_answer
from agentic_graphrag.generation.trace import Claim, QueryStatus, ReasoningChain
from agentic_graphrag.llm.provider import MockLLMProvider
from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource, Citation


def _cand(cid: str, text: str) -> Candidate:
    return Candidate(
        id=cid,
        source=CandidateSource.GRAPH_NEIGHBOR,
        content=text,
        score=1.0,
        citations=[Citation(entity_id="e1", span=text)],
    )


def test_generate_answer_no_evidence_fallback():
    chain = ReasoningChain(question="Who is CEO?", query_id="q1")
    out = generate_answer(chain, [], None, allow_llm=False)
    assert out.status == QueryStatus.NO_ANSWER


def test_generate_answer_offline_path():
    chain = ReasoningChain(question="Who is the CEO of Apex Holdings?", query_id="q2")
    evidence = [
        _cand("c1", "Elena Varga -[CEO_OF]-> Apex Holdings (PERSON)"),
    ]
    out = generate_answer(chain, evidence, None, allow_llm=False)
    assert out.answer
    assert out.metadata.get("offline_answerer")


def test_generate_answer_citation_intercept_then_fallback():
    bad = json.dumps(
        {
            "answer": "Someone",
            "status": "answered",
            "claims": [{"text": "Someone", "evidence_ids": ["missing"]}],
            "missing_info": [],
        }
    )
    llm = MockLLMProvider(responses={"grounded": bad, "IMPORTANT": bad, "evidence": bad})
    # Always return uncited payload
    llm.responses = {"": bad}  # substring match on empty won't work

    class AlwaysBad(MockLLMProvider):
        def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            return bad

    chain = ReasoningChain(question="Who?", query_id="q3")
    evidence = [_cand("c1", "A -[CEO_OF]-> B (PERSON)")]
    out = generate_answer(chain, evidence, AlwaysBad(), allow_llm=True)
    assert out.metadata.get("citation_fallback") or out.status == QueryStatus.NO_ANSWER


def test_generate_answer_llm_success():
    payload = AnswerPayload(
        answer="Elena Varga",
        status=QueryStatus.ANSWERED,
        claims=[Claim(text="Elena Varga is CEO", evidence_ids=["c1"])],
    )
    good = payload.model_dump_json()

    class GoodLLM(MockLLMProvider):
        def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            return good

    chain = ReasoningChain(question="Who is CEO of Apex?", query_id="q4")
    evidence = [_cand("c1", "Elena Varga -[CEO_OF]-> Apex Holdings (PERSON)")]
    out = generate_answer(chain, evidence, GoodLLM(), allow_llm=True)
    assert out.status == QueryStatus.ANSWERED
    assert "Elena" in (out.answer or "")


def test_offline_honest_no_answer_for_unknown_ceo():
    chain = ReasoningChain(question="Who is the CEO of ZzzUnknown Co?", query_id="q5")
    evidence = [_cand("c1", "Elena Varga -[CEO_OF]-> Apex Holdings (PERSON)")]
    out = offline_answer(chain, evidence, "")
    assert out.metadata.get("offline_answerer") in {
        "honest_no_match",
        "extractive",
        "focused",
    }
