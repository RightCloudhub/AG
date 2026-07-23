"""Recover a usable ReasoningChain when LangGraph hits recursion_limit.

Design (agent-orchestration): recursion_limit is the final backstop — on hit we
must still emit a partial/honest answer, never a bare exception name to clients.
"""

from __future__ import annotations

from typing import Any

from agentic_graphrag.generation.answer import generate_answer
from agentic_graphrag.generation.trace import ReasoningChain
from agentic_graphrag.llm.budget import BudgetTracker
from agentic_graphrag.llm.provider import LLMProvider
from agentic_graphrag.retrieval.contracts import Candidate

_RECURSION_REASON = "agent recursion_limit reached (guardrail backstop)"


def invoke_agentic_graph(
    graph: Any,
    *,
    question: str,
    chain: ReasoningChain,
    tid: str,
    rec_limit: int,
    budget: BudgetTracker | None,
    t0: float,
    llm: LLMProvider | None = None,
    allow_llm: bool = False,
) -> ReasoningChain:
    """Invoke compiled graph; recover a partial chain on GraphRecursionError."""
    from agentic_graphrag.agent.loop import finalize_agentic_chain, invoke_config

    initial = {
        "question": question,
        "chain": chain.model_dump(),
        "sub_questions": [],
        "current_index": 0,
        "hop": 0,
        "evidence": [],
        "done": False,
        "allow_llm": allow_llm,
    }
    try:
        result = graph.invoke(initial, config=invoke_config(tid, recursion_limit=rec_limit))
    except Exception as exc:  # noqa: BLE001 — recursion backstop
        if type(exc).__name__ != "GraphRecursionError":
            raise
        return recover_chain_after_recursion(
            graph,
            thread_id=tid,
            question=question,
            budget=budget,
            t0=t0,
            llm=llm,
            allow_llm=allow_llm,
        )
    return finalize_agentic_chain(result, budget=budget, tid=tid, t0=t0)


def recover_chain_after_recursion(
    graph: Any,
    *,
    thread_id: str,
    question: str,
    budget: BudgetTracker | None,
    t0: float,
    llm: LLMProvider | None = None,
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
    meta = dict(chain.metadata or {})
    meta["recursion_limit_hit"] = True
    meta["guardrail_status"] = _RECURSION_REASON
    chain.metadata = meta
    payload = {
        "chain": chain.model_dump(),
        "evidence": [c.model_dump() for c in evidence],
        "memory_snapshot": (state or {}).get("memory_snapshot") or {},
    }
    return finalize_agentic_chain(payload, budget=budget, tid=thread_id, t0=t0)


def _checkpoint_values(graph: Any, thread_id: str) -> dict[str, Any] | None:
    try:
        from agentic_graphrag.agent.loop import invoke_config

        snap = graph.get_state(invoke_config(thread_id))
    except Exception:  # noqa: BLE001 — recovery must not raise
        return None
    if snap is None:
        return None
    values = getattr(snap, "values", None)
    return values if isinstance(values, dict) else None


def _chain_from_state(state: dict[str, Any] | None, question: str) -> ReasoningChain:
    if state and state.get("chain"):
        try:
            return ReasoningChain.model_validate(state["chain"])
        except Exception:  # noqa: BLE001
            pass
    return ReasoningChain(question=question, route="agentic")


def _evidence_from_state(state: dict[str, Any] | None) -> list[Candidate]:
    if not state:
        return []
    out: list[Candidate] = []
    for raw in state.get("evidence") or []:
        try:
            out.append(Candidate.model_validate(raw))
        except Exception:  # noqa: BLE001
            continue
    return out


def _conclusions_from_state(state: dict[str, Any] | None) -> str:
    if not state:
        return ""
    snap = state.get("memory_snapshot") or {}
    conc = snap.get("conclusions") or []
    if isinstance(conc, list):
        return "; ".join(str(c) for c in conc if c)
    return ""
