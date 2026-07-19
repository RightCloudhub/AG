from agentic_graphrag.generation.trace import QueryStatus, ReasoningChain
from agentic_graphrag.retrieval.contracts import (
    Candidate,
    CandidateSource,
    Citation,
    channel_of,
    concat_candidates,
    normalize_source,
    rrf_fuse,
)


def test_candidate_model_fine_grained_type():
    c = Candidate(
        id="c1",
        source=CandidateSource.VECTOR_CHUNK,
        content="hello",
        score=0.5,
        citations=[Citation(doc_id="d1", chunk_id="d1:0")],
    )
    dump = c.model_dump()
    assert dump["source"] == "vector_chunk"
    assert dump["type"] == "vector_chunk"
    assert c.channel == "vector"
    assert c.citation_keys() == ["chunk:d1:0"]


def test_legacy_source_normalized():
    c = Candidate(id="x", source=CandidateSource.GRAPH, content="A -[R]-> B")
    assert c.source == CandidateSource.GRAPH_NEIGHBOR
    assert c.type == "graph_neighbor"
    assert c.is_graph()
    assert normalize_source("graph", kind="path") == CandidateSource.GRAPH_PATH
    assert channel_of("fulltext") == "fulltext"


def test_concat_dedupes_same_channel():
    a = Candidate(id="x", source=CandidateSource.VECTOR_CHUNK, content="a")
    b = Candidate(id="x", source=CandidateSource.VECTOR_CHUNK, content="b")
    c = Candidate(id="x", source=CandidateSource.FULLTEXT_CHUNK, content="c")
    out = concat_candidates([a], [b, c])
    assert len(out) == 2


def test_rrf_fuse_ranks_by_reciprocal_rank():
    v = [
        Candidate(id="a", source=CandidateSource.VECTOR_CHUNK, content="a", score=0.9),
        Candidate(id="b", source=CandidateSource.VECTOR_CHUNK, content="b", score=0.8),
    ]
    f = [
        Candidate(id="b", source=CandidateSource.FULLTEXT_CHUNK, content="b", score=5.0),
        Candidate(id="c", source=CandidateSource.FULLTEXT_CHUNK, content="c", score=1.0),
    ]
    fused = rrf_fuse(v, f, k=60)
    assert fused[0].source == CandidateSource.FUSION
    ids = [c.id for c in fused]
    assert "b" in ids  # appears in both lists → higher RRF
    assert ids.index("b") < ids.index("c")


def test_reasoning_chain_honest_fallback():
    chain = ReasoningChain(question="q?", explored_paths=["A->B"])
    chain.honest_fallback("no evidence")
    assert chain.status == QueryStatus.NO_ANSWER
    assert "无法基于现有知识回答" in chain.answer
    assert "A->B" in chain.answer
