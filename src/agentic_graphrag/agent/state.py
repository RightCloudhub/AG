"""AgentState: the explicit, versioned contract of the agent loop (refactor §1.4).

There is exactly one definition and one constructor. Checkpoints carry
``state_version`` so a resumed run (another instance, a human-in-the-loop
pause) can detect and migrate an older shape instead of misreading it.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.plan_dag import SubQuestion
from agentic_graphrag.agent.register_state import RegisterRuntimeState, merge_register_runtime
from agentic_graphrag.agent.requirements import Register
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ReasoningChain, ToolCallTrace

STATE_SCHEMA_VERSION = 3

type Retrieved = tuple[list[Candidate], list[ToolCallTrace]]


class EvidenceGap(TypedDict):
    missing: list[str]


def merge_evidence(
    previous: dict[str, Candidate], incoming: dict[str, Candidate]
) -> dict[str, Candidate]:
    """Keep the first candidate for each id, matching MemoryState.add_evidence."""
    result = dict(previous)
    for key, candidate in incoming.items():
        result.setdefault(key, candidate)
    return result


class AgentState(TypedDict, total=False):
    """LangGraph typed state (P2-AG-03). Memory semantics live in MemoryState."""

    state_version: int
    question: str
    chain: ReasoningChain
    sub_questions: list[SubQuestion]
    current_index: int
    hop: int
    evidence: Annotated[dict[str, Candidate], merge_evidence]
    memory_summary: str
    memory: MemoryState
    done: bool
    guardrail_status: str
    allow_llm: bool
    tenant_id: str
    dropped_sub_questions: list[str]
    prefetched: dict[str, Retrieved]
    # Structured "missing evidence" signal from the answer node (refactor §1.5).
    evidence_gap: EvidenceGap
    answer_retries: int
    register: Register | None
    register_runtime: Annotated[RegisterRuntimeState, merge_register_runtime]


def active_register(state: AgentState) -> Register:
    """Require the register initialized by the register planner."""
    register = state.get("register")
    if register is None:
        raise ValueError("register planner has not initialized the register")
    return register


def initial_state(
    question: str,
    chain: ReasoningChain,
    *,
    allow_llm: bool,
    tenant_id: str = "",
    evidence: list[Candidate] | None = None,
) -> AgentState:
    """Build the initial state. ``tenant_id`` MUST be set: omitting it made runs
    retrieve with ``tenant_id=None`` ("no filter") → cross-tenant leak (BL-04).

    Every channel is set: one left out keeps its value from the previous run when a
    caller reuses a thread.
    """
    return AgentState(
        state_version=STATE_SCHEMA_VERSION,
        question=question,
        chain=chain,
        sub_questions=[],
        current_index=0,
        hop=0,
        evidence={candidate.id: candidate for candidate in evidence or []},
        memory_summary="",
        memory=MemoryState(),
        register=None,
        register_runtime=RegisterRuntimeState(),
        done=False,
        guardrail_status="",
        allow_llm=allow_llm,
        tenant_id=tenant_id,
        dropped_sub_questions=[],
        prefetched={},
        evidence_gap=EvidenceGap(missing=[]),
        answer_retries=0,
    )
