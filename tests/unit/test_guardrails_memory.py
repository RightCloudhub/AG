import pytest

from agentic_graphrag.agent.guardrails import GuardrailConfig, Guardrails
from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.llm.budget import BudgetExceeded, BudgetTracker
from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource


def test_budget_trips_on_calls():
    b = BudgetTracker(max_llm_calls=2, max_tokens=1000)
    b.record_call(1, 1)
    b.record_call(1, 1)
    with pytest.raises(BudgetExceeded):
        b.record_call(1, 1)


def test_guardrails_max_hops():
    g = Guardrails(GuardrailConfig(max_hops=2, max_llm_calls=100, max_tokens=100000))
    g.on_hop_start()
    g.on_hop_start()
    assert not g.state.tripped
    g.on_hop_start()
    assert g.state.tripped
    assert "max_hops" in g.state.reason


def test_memory_dedupe_subquestion():
    m = MemoryState()
    assert not m.is_duplicate_subquestion("Who is CEO of Apex?")
    m.mark_subquestion("Who is CEO of Apex?")
    assert m.is_duplicate_subquestion("who is ceo of apex?")


def test_memory_add_evidence():
    m = MemoryState()
    c = Candidate(
        id="e1",
        source=CandidateSource.GRAPH,
        content="A -[CEO_OF]-> B",
        score=1.0,
    )
    added = m.add_evidence([c, c])
    assert added == ["e1"]
    assert len(m.evidence) == 1
    assert m.is_path_explored("A -[CEO_OF]-> B")
