"""Blackboard reducer, checkpoint and migration contracts."""

import pytest
from langgraph.graph import END, START, StateGraph

from agentic_graphrag.agent.blackboard import BudgetEntry, Proposal, append_records, usage_totals
from agentic_graphrag.agent.requirements import Requirement
from agentic_graphrag.agent.state import AgentState, initial_state
from agentic_graphrag.agent.state_migration import migrate_state
from agentic_graphrag.agent.state_serde import AgentStateSerializer
from agentic_graphrag.core.reasoning import ReasoningChain


def proposal(branch="a"):
    return Proposal(
        entry_id=branch,
        branch_id=branch,
        tenant_id="tenant",
        run_id="run",
        requirement=Requirement(req_id=branch, text="Find the owner"),
    )


def test_parallel_proposals_append_without_writing_register():
    graph = StateGraph(AgentState)
    for name in ("a", "b"):
        graph.add_node(name, lambda _, name=name: {"proposals": [proposal(name)]})
        graph.add_edge(START, name)
        graph.add_edge(name, END)
    initial = initial_state("q", ReasoningChain(question="q"), allow_llm=False)
    result = graph.compile().invoke(initial)
    assert [item.branch_id for item in result["proposals"]] == ["a", "b"]
    assert result["register"] is None and initial["proposals"] == []
    assert append_records(result["proposals"], [proposal()]) == result["proposals"]
    with pytest.raises(ValueError, match="conflicting blackboard"):
        append_records([proposal()], [proposal().model_copy(update={"run_id": "other"})])


def test_ledger_is_attributable_and_allocations_are_not_usage():
    usage = BudgetEntry(
        entry_id="call",
        branch_id="a",
        tenant_id="t",
        run_id="r",
        req_id="a",
        role="researcher",
        calls=1,
        prompt_tokens=10,
        completion_tokens=5,
    )
    allocation = usage.model_copy(update={"entry_id": "reserve", "kind": "allocation"})
    assert usage_totals([usage, allocation])["total_tokens"] == 15
    assert usage_totals([usage])["llm_calls"] == 1
    serde = AgentStateSerializer()
    for value in ([proposal()], [usage, allocation]):
        assert serde.loads_typed(serde.dumps_typed(value)) == value


def test_v3_checkpoint_keeps_typed_evidence_and_does_not_invent_pending_writes():
    state = initial_state("q", ReasoningChain(question="q"), allow_llm=False)
    state["state_version"] = 3
    for key in ("proposals", "events", "budget_ledger"):
        state.pop(key)
    restored = migrate_state(state)
    assert restored["state_version"] == 4
    assert restored["evidence"] == {} and restored["register"] is None
    assert "proposals" not in restored
