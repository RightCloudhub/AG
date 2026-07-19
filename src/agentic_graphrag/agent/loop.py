"""LangGraph StateGraph agent loop assembly (ADR-005, FR-AG-02~07).

Node handlers live in :mod:`agentic_graphrag.agent.loop_runtime`.
This module wires the StateGraph, attaches a checkpointer (P2-AG-03),
and exposes :func:`run_agentic_query`.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from agentic_graphrag.agent.checkpointer import default_checkpointer
from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop_runtime import AgentRuntime, AgentState
from agentic_graphrag.generation.trace import ReasoningChain
from agentic_graphrag.llm.budget import BudgetTracker
from agentic_graphrag.llm.provider import LLMProvider

__all__ = [
    "AgentState",
    "build_graph",
    "invoke_config",
    "run_agentic_query",
]


def build_graph(
    executor: Executor,
    llm: LLMProvider | None,
    guard_cfg: GuardrailConfig,
    budget: BudgetTracker | None = None,
    *,
    checkpointer: Any | None = None,
):
    """Compile a LangGraph StateGraph for the agentic loop.

    Always attaches a checkpointer so node state (including
    ``memory_snapshot``) is durable across steps and recoverable via
    ``get_state`` / ``get_state_history`` for the same ``thread_id``.
    """
    from langgraph.graph import END, StateGraph

    rt = AgentRuntime(executor, llm, guard_cfg, budget=budget)
    cp = checkpointer if checkpointer is not None else default_checkpointer()

    g = StateGraph(AgentState)
    g.add_node("planner", rt.node_planner)
    g.add_node("executor", rt.node_executor)
    g.add_node("critic", rt.node_critic)
    g.add_node("answer", rt.node_answer)
    g.set_entry_point("planner")
    g.add_edge("planner", "executor")
    g.add_edge("executor", "critic")
    g.add_conditional_edges(
        "critic", rt.route_after_critic, {"executor": "executor", "answer": "answer"}
    )
    g.add_edge("answer", END)
    return g.compile(checkpointer=cp)


def invoke_config(
    thread_id: str,
    *,
    recursion_limit: int = 15,
) -> dict[str, Any]:
    """RunnableConfig for checkpointer-backed invoke / get_state."""
    return {
        "recursion_limit": recursion_limit,
        "configurable": {"thread_id": thread_id},
    }


def run_agentic_query(
    question: str,
    executor: Executor,
    llm: LLMProvider | None,
    *,
    guard_cfg: GuardrailConfig | None = None,
    budget: BudgetTracker | None = None,
    allow_llm: bool = True,
    recursion_limit: int | None = None,
    checkpointer: Any | None = None,
    thread_id: str | None = None,
) -> ReasoningChain:
    """Run the full agentic loop and return a reasoning chain.

    When ``guard_cfg`` is omitted, limits are loaded from application config
    (P2-AG-04). ``recursion_limit`` defaults to ``guard_cfg.recursion_limit``.

    ``thread_id`` keys the checkpointer (defaults to the chain ``query_id``)
    so final state and history remain queryable after return.
    """
    guard_cfg = guard_cfg or GuardrailConfig.from_app_config()
    budget = budget or guard_cfg.budget_tracker()
    rec_limit = recursion_limit if recursion_limit is not None else guard_cfg.recursion_limit
    chain = ReasoningChain(question=question, route="agentic")
    tid = thread_id or chain.query_id or str(uuid4())
    graph = build_graph(
        executor, llm, guard_cfg, budget=budget, checkpointer=checkpointer
    )
    t0 = time.perf_counter()
    config = invoke_config(tid, recursion_limit=rec_limit)
    result = graph.invoke(
        {
            "question": question,
            "chain": chain.model_dump(),
            "sub_questions": [],
            "current_index": 0,
            "hop": 0,
            "evidence": [],
            "done": False,
            "allow_llm": allow_llm,
        },
        config=config,
    )
    out = ReasoningChain.model_validate(result["chain"])
    out.cost.latency_ms = int((time.perf_counter() - t0) * 1000)
    if budget:
        snap = budget.snapshot()
        out.cost.llm_calls = snap["llm_calls"]
        out.cost.tokens = snap["total_tokens"]
        out.cost.prompt_tokens = snap["prompt_tokens"]
        out.cost.completion_tokens = snap["completion_tokens"]
    meta = dict(out.metadata or {})
    meta["thread_id"] = tid
    meta["checkpointer"] = True
    # Surface final memory snapshot size for debugging / audit hooks
    mem_snap = result.get("memory_snapshot") or {}
    meta["memory_evidence_count"] = len(mem_snap.get("evidence") or [])
    out.metadata = meta
    return out
