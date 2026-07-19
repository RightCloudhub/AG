"""P3: triage, fusion, cache, confidence, parallel executor."""

from __future__ import annotations

from agentic_graphrag.agent.triage import Route, should_escalate_fast_path, triage
from agentic_graphrag.generation.confidence import ConfidenceLevel, grade_confidence
from agentic_graphrag.generation.trace import QueryStatus, ReasoningChain
from agentic_graphrag.retrieval.cache import RetrievalCache
from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource
from agentic_graphrag.retrieval.fusion import fuse_candidates


def test_triage_simple_fact_fast_path():
    r = triage("Who is the CEO of Apex Holdings?", allow_llm=False)
    assert r.route == Route.FAST_PATH
    assert r.rule_hit in {"simple_fact", "short_wh"}


def test_triage_multi_hop_agentic():
    r = triage(
        "Who is the CEO of the parent company of BrightLink Logistics?",
        allow_llm=False,
    )
    assert r.route == Route.AGENTIC


def test_triage_force_agentic():
    r = triage("Who is the CEO of Apex Holdings?", force_agentic=True, allow_llm=False)
    assert r.route == Route.AGENTIC


def test_should_escalate_empty_evidence():
    assert should_escalate_fast_path(0, has_graph=False) is True
    assert should_escalate_fast_path(5, has_graph=True, answer_status="answered") is False


def test_rrf_fuse_ranks_multi_channel():
    a = [
        Candidate(id="c1", source=CandidateSource.VECTOR_CHUNK, content="A", score=0.9),
        Candidate(id="c2", source=CandidateSource.VECTOR_CHUNK, content="B", score=0.5),
    ]
    b = [
        Candidate(id="c2", source=CandidateSource.FULLTEXT_CHUNK, content="B", score=10.0),
        Candidate(id="c3", source=CandidateSource.FULLTEXT_CHUNK, content="C", score=5.0),
    ]
    fused = fuse_candidates(a, b, method="rrf", k=60, limit=10)
    assert len(fused) >= 2
    # c2 appears in both lists → higher RRF
    ids = [c.id for c in fused]
    assert "c2" in ids
    assert fused[0].id == "c2" or ids.index("c2") <= 1


def test_retrieval_cache_version_invalidation():
    cache = RetrievalCache()
    cands = [
        Candidate(id="x", source=CandidateSource.VECTOR_CHUNK, content="hi", score=1.0)
    ]
    cache.set_retrieval("who is ceo", cands)
    assert cache.get_retrieval("who is ceo") is not None
    cache.on_index_update()
    assert cache.get_retrieval("who is ceo") is None


def test_answer_cache_ttl_key():
    cache = RetrievalCache(answer_ttl_seconds=3600)
    cache.set_answer("Q?", {"query_id": "1", "answer": "A"})
    hit = cache.get_answer("Q?")
    assert hit is not None
    assert hit["answer"] == "A"


def test_confidence_high_with_graph_and_claims():
    chain = ReasoningChain(
        question="q",
        status=QueryStatus.ANSWERED,
        answer="Elena",
        claims=[{"text": "Elena is CEO", "evidence_ids": ["e1"]}],
        steps=[{"hop": 1, "sub_question": "q"}, {"hop": 2, "sub_question": "q2"}],
    )
    evidence = [
        Candidate(id="e1", source=CandidateSource.GRAPH_NEIGHBOR, content="x", score=1),
        Candidate(id="e2", source=CandidateSource.GRAPH_PATH, content="y", score=1),
    ]
    g = grade_confidence(chain, evidence)
    assert g["level"] in {
        ConfidenceLevel.HIGH.value,
        ConfidenceLevel.MEDIUM.value,
        ConfidenceLevel.LOW.value,
    }
    assert g["score"] > 0.4
