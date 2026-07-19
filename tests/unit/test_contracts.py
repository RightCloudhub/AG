from agentic_graphrag.generation.trace import QueryStatus, ReasoningChain
from agentic_graphrag.retrieval.contracts import (
    Candidate,
    CandidateSource,
    Citation,
    concat_candidates,
)


def test_candidate_model():
    c = Candidate(
        id="c1",
        source=CandidateSource.VECTOR,
        content="hello",
        score=0.5,
        citations=[Citation(doc_id="d1", chunk_id="d1:0")],
    )
    assert c.model_dump()["source"] == "vector"


def test_concat_dedupes():
    a = Candidate(id="x", source=CandidateSource.VECTOR, content="a")
    b = Candidate(id="x", source=CandidateSource.VECTOR, content="b")
    c = Candidate(id="x", source=CandidateSource.FULLTEXT, content="c")
    out = concat_candidates([a], [b, c])
    assert len(out) == 2


def test_reasoning_chain_honest_fallback():
    chain = ReasoningChain(question="q?", explored_paths=["A->B"])
    chain.honest_fallback("no evidence")
    assert chain.status == QueryStatus.NO_ANSWER
    assert "无法基于现有知识回答" in chain.answer
    assert "A->B" in chain.answer
