"""Copy-on-write register memory and terminal round updates."""

from dataclasses import replace

from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.requirements import Register, Requirement
from agentic_graphrag.agent.state import AgentState, active_register
from agentic_graphrag.core.enums import CriticAction, RequirementStatus
from agentic_graphrag.core.evidence import Candidate


def fallback_register(question: str) -> Register:
    return Register(
        question=question,
        items={
            "req_0": Requirement(req_id="req_0", text=question, status=RequirementStatus.ACTIVE)
        },
    )


def with_register_memory(state: AgentState) -> AgentState:
    memory = MemoryState()
    memory.add_evidence(list(state.get("evidence", {}).values()))
    for item in active_register(state).items.values():
        if item.status == RequirementStatus.RESOLVED:
            memory.mark_subquestion_done(item.req_id, item.notes)
    return {**state, "memory": memory}


def stopped_state(state: AgentState, reason: str, *, clear_progress: bool = False) -> AgentState:
    runtime = state["register_runtime"]
    return {
        **state,
        "done": True,
        "register_runtime": replace(
            runtime, stop=reason, progress="" if clear_progress else runtime.progress
        ),
    }


def finish_register_round(
    state: AgentState,
    register: Register,
    fresh: list[Candidate],
    *,
    seen: list[str],
    stop: str,
) -> AgentState:
    runtime = state["register_runtime"]
    chain = state["chain"].model_copy(deep=True)
    chain.steps[-1].conclusion = runtime.progress or "No requirement was closed."
    chain.steps[-1].critic_action = (
        CriticAction.SUFFICIENT if register.is_clear() else CriticAction.NEXT_HOP
    )
    return with_register_memory(
        {
            **state,
            "chain": chain,
            "done": bool(stop),
            "register_runtime": replace(
                runtime, seen=sorted(set(seen) | {c.id for c in fresh}), stop=stop
            ),
        }
    )
