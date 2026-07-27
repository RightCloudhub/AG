from agentic_graphrag.generation.answer import generate_answer
from agentic_graphrag.generation.citations import (
    _content_tokens,
    claims_bind_to_evidence,
    claims_have_citations,
    claims_lexically_supported,
    validate_answered_claims,
)
from agentic_graphrag.generation.trace import Claim, QueryStatus, ReasoningChain
from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource


def test_claims_require_evidence_ids():
    assert not claims_have_citations([])
    assert not claims_have_citations([Claim(text="x", evidence_ids=[])])
    assert claims_have_citations([Claim(text="x", evidence_ids=["e1"])])


def test_claims_must_bind_to_retrieved_ids():
    evidence = [
        Candidate(id="e1", source=CandidateSource.GRAPH_NEIGHBOR, content="A -[R]-> B"),
    ]
    assert claims_bind_to_evidence([Claim(text="A", evidence_ids=["e1"])], evidence)
    assert not claims_bind_to_evidence([Claim(text="A", evidence_ids=["missing"])], evidence)


def test_validate_answered_claims():
    evidence = [
        Candidate(id="e1", source=CandidateSource.VECTOR_CHUNK, content="fact"),
    ]
    assert validate_answered_claims([], evidence) == "no claims"
    assert (
        validate_answered_claims([Claim(text="x", evidence_ids=["nope"])], evidence)
        == "claim evidence_ids not in retrieved set"
    )
    assert validate_answered_claims([Claim(text="x", evidence_ids=["e1"])], evidence) is None


def test_offline_answer_binds_citations():
    chain = ReasoningChain(question="Who is the CEO of Apex Holdings?")
    evidence = [
        Candidate(
            id="nbr:1",
            source=CandidateSource.GRAPH_NEIGHBOR,
            content="Elena Varga -[CEO_OF]-> Apex Holdings (Person)",
            score=1.0,
        )
    ]
    out = generate_answer(chain, evidence, None, allow_llm=False)
    assert out.status in (QueryStatus.ANSWERED, QueryStatus.PARTIAL)
    assert out.claims
    assert all(c.evidence_ids for c in out.claims)


def test_content_tokens_english():
    """English text splits on whitespace as expected."""
    toks = _content_tokens("Elena Varga is the CEO of Apex Holdings")
    assert "elena" in toks
    assert "varga" in toks
    assert "apex" in toks
    assert "holdings" in toks
    assert all(stop not in toks for stop in ("the", "is", "of"))  # stopwords removed


def test_content_tokens_cjk_unigrams():
    """CJK characters are extracted as individual unigrams."""
    toks = _content_tokens("苹果控股的首席执行官是埃琳娜·瓦尔加")
    # Each CJK character becomes its own token
    assert "苹" in toks
    assert "果" in toks
    assert "控" in toks
    assert "股" in toks
    assert "首" in toks
    assert "席" in toks
    assert "埃" in toks
    assert "娜" in toks
    assert "瓦" in toks
    assert "加" in toks
    # Non-CJK (· middle dot) is filtered out by isalnum
    assert "·" not in toks


def test_claims_lexically_supported_cjk():
    """Chinese claim with CJK evidence passes lexical gate (BL-02 fix)."""
    evidence = [
        Candidate(
            id="e1",
            source=CandidateSource.GRAPH_NEIGHBOR,
            content="Apex Holdings -[CEO_OF]-> Elena Varga (Person)",
        ),
    ]
    # Claim with mixed CJK + Latin tokens
    claim = Claim(text="Apex Holdings的首席执行官是Elena Varga。", evidence_ids=["e1"])
    # CJK unigrams + Latin tokens should find overlap
    assert claims_lexically_supported([claim], evidence, min_overlap=1)


def test_claims_lexically_supported_pure_cjk():
    """Pure CJK claim with a CJK evidence candidate passes lexical gate."""
    evidence = [
        Candidate(
            id="e1",
            source=CandidateSource.GRAPH_NEIGHBOR,
            content="张三是Apex Holdings的首席执行官",
        ),
    ]
    claim = Claim(text="Apex Holdings的首席执行官是张三", evidence_ids=["e1"])
    # Should share "首" "席" "执" "行" "官" "张" "三" "apex" "holdings"
    assert claims_lexically_supported([claim], evidence, min_overlap=1)


def test_claims_lexically_supported_default_min_overlap():
    """Test that the default min_overlap=2 works correctly for English claims.

    Δ The default was changed from 1 to 2 (BL-13).  This test exercises the
    production path (no explicit min_overlap) to ensure the new default is
    not overly strict for legitimate claims.
    """
    evidence = [
        Candidate(
            id="e1",
            source=CandidateSource.GRAPH_NEIGHBOR,
            content="Elena Varga is the CEO of Apex Holdings",
        ),
    ]
    # Claim shares 3+ tokens with evidence → should pass with default min_overlap=2
    claim = Claim(text="Elena Varga is CEO of Apex Holdings", evidence_ids=["e1"])
    assert claims_lexically_supported([claim], evidence)

    # Claim shares only 1 token with evidence → should fail with default min_overlap=2
    claim2 = Claim(text="Elena related", evidence_ids=["e1"])
    assert not claims_lexically_supported([claim2], evidence)
