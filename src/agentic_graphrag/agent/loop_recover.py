"""Recover a usable ReasoningChain when LangGraph hits recursion_limit.

Design (agent-orchestration): recursion_limit is the final backstop — on hit we
must still emit a partial/honest answer, never a bare exception name to clients.
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from agentic_graphrag.agent.graph_types import AgentGraph
from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.state import AgentState
from agentic_graphrag.core.enums import QueryRoute
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.generation.answer import generate_answer
from agentic_graphrag.governance.budget import BudgetTracker

_RECURSION_REASON = "agent recursion_limit reached (guardrail backstop)"


def recover_chain_after_recursion(
    graph: AgentGraph,
    *,
    thread_id: str,
    question: str,
    budget: BudgetTracker | None,
    t0: float,
    llm: LLMClient | None = None,
    allow_llm: bool = False,
) -> ReasoningChain:
    """Build a chain from checkpointed state after GraphRecursionError."""
    # Lazy import avoids loop ↔ loop_recover cycle (finalize lives in loop).
    from agentic_graphrag.agent.loop import finalize_agentic_chain

    state = _checkpoint_values(graph, thread_id)
    chain = _chain_from_state(state, question)
    evidence = _evidence_from_state(state)
    conclusions = _conclusions_from_state(state)
    if evidence and not chain.answer:
        chain = generate_answer(
            chain,
            evidence,
            llm,
            conclusions=conclusions,
            guardrail_status=_RECURSION_REASON,
            allow_llm=allow_llm and llm is not None,
        )
    if not chain.answer:
        chain.honest_fallback(_RECURSION_REASON)
    chain = _annotate_register_recovery(chain, state)
    meta = dict(chain.metadata or {})
    meta["recursion_limit_hit"] = True
    meta["guardrail_status"] = _RECURSION_REASON
    chain.metadata = meta
    payload: AgentState = {
        "chain": chain,
        "evidence": {c.id: c for c in evidence},
        "memory": state.get("memory", MemoryState()) if state is not None else MemoryState(),
    }
    return finalize_agentic_chain(payload, budget=budget, tid=thread_id, t0=t0)


def _annotate_register_recovery(chain: ReasoningChain, state: AgentState | None) -> ReasoningChain:
    if not state or state.get("register") is None:
        return chain
    from agentic_graphrag.agent.register_answer import annotate_register

    resumed: AgentState = {
        **state,
        "register_runtime": replace(state["register_runtime"], stop="recursion_limit"),
    }
    return annotate_register(chain, resumed)


def _checkpoint_values(graph: AgentGraph, thread_id: str) -> AgentState | None:
    try:
        from agentic_graphrag.agent.loop import invoke_config

        snap = graph.get_state(invoke_config(thread_id))
    except Exception:  # noqa: BLE001 — recovery must not raise
        return None
    if snap is None:
        return None
    values = snap.values
    return cast(AgentState, values) if isinstance(values, dict) else None


def _chain_from_state(state: AgentState | None, question: str) -> ReasoningChain:
    if state and isinstance(state.get("chain"), ReasoningChain):
        return state["chain"].model_copy(deep=True)
    return ReasoningChain(question=question, route=QueryRoute.AGENTIC)


def _evidence_from_state(state: AgentState | None) -> list[Candidate]:
    if not state:
        return []
    return [c for c in state.get("evidence", {}).values() if isinstance(c, Candidate)]


def _conclusions_from_state(state: AgentState | None) -> str:
    if not state:
        return ""
    memory = state.get("memory")
    if memory is not None:
        return "; ".join(c for c in memory.conclusions if c)
    return ""
