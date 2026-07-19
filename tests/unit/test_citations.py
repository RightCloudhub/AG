from agentic_graphrag.generation.answer import generate_answer
from agentic_graphrag.generation.citations import (
    claims_bind_to_evidence,
    claims_have_citations,
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
