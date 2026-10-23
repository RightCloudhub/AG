"""AgentState: the explicit, versioned contract of the agent loop (refactor §1.4).

There is exactly one definition and one constructor. Checkpoints carry
``state_version`` so a resumed run (another instance, a human-in-the-loop
pause) can detect and migrate an older shape instead of misreading it.
"""

from __future__ import annotations

from typing import Any, TypedDict

STATE_SCHEMA_VERSION = 2


class AgentState(TypedDict, total=False):
    """LangGraph typed state (P2-AG-03). Memory semantics live in MemoryState."""

    state_version: int
    question: str
    chain: dict[str, Any]
    sub_questions: list[dict[str, Any]]
    current_index: int
    hop: int
    evidence: list[dict[str, Any]]
    memory_summary: str
    memory_snapshot: dict[str, Any]
    done: bool
    guardrail_status: str
    allow_llm: bool
    tenant_id: str
    dropped_sub_questions: list[str]
    prefetched: dict[str, Any]
    # Structured "missing evidence" signal from the answer node (refactor §1.5).
    evidence_gap: dict[str, Any]
    answer_retries: int
    requirement_register: dict[str, Any]
    register_context: list[dict[str, str]]
    register_events: list[dict[str, Any]]
    register_cache: dict[str, list[str]]
    register_seen: list[str]
    register_attempts: dict[str, int]
    register_fresh: list[str]
    register_stop: str
    register_progress: str
    register_answer_policy: str


def initial_state(
    question: str,
    chain: dict[str, Any],
    *,
    allow_llm: bool,
    tenant_id: str = "",
    evidence: list[dict[str, Any]] | None = None,
) -> AgentState:
    """Build the initial state. ``tenant_id`` MUST be set: omitting it made runs
    retrieve with ``tenant_id=None`` ("no filter") → cross-tenant leak (BL-04)."""
    return AgentState(
        state_version=STATE_SCHEMA_VERSION,
        question=question,
        chain=chain,
        sub_questions=[],
        current_index=0,
        hop=0,
        evidence=list(evidence or []),
        done=False,
        allow_llm=allow_llm,
        tenant_id=tenant_id,
        answer_retries=0,
    )


def check_state_version(state: dict[str, Any]) -> int:
    """Return the state's schema version; raise on a newer, unknown shape."""
    version = int(state.get("state_version") or 0)
    if version > STATE_SCHEMA_VERSION:
        raise ValueError(
            f"agent state version {version} is newer than supported {STATE_SCHEMA_VERSION}"
        )
    return version
