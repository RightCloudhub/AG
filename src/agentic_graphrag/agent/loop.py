"""LangGraph StateGraph agent loop assembly (ADR-005, FR-AG-02~07).

Node handlers live in :mod:`agentic_graphrag.agent.loop_runtime`.
This module wires the StateGraph and exposes :func:`run_agentic_query`.
"""

from __future__ import annotations

import time

from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop_runtime import AgentRuntime, AgentState
from agentic_graphrag.generation.trace import ReasoningChain
from agentic_graphrag.llm.budget import BudgetTracker
from agentic_graphrag.llm.provider import LLMProvider

__all__ = [
    "AgentState",
    "build_graph",
    "run_agentic_query",
]


def build_graph(
    executor: Executor,
    llm: LLMProvider | None,
    guard_cfg: GuardrailConfig,
    budget: BudgetTracker | None = None,
):
    """Compile a LangGraph StateGraph for the agentic loop."""
    from langgraph.graph import END, StateGraph

    rt = AgentRuntime(executor, llm, guard_cfg, budget=budget)

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
    return g.compile()


def run_agentic_query(
    question: str,
    executor: Executor,
    llm: LLMProvider | None,
    *,
    guard_cfg: GuardrailConfig | None = None,
    budget: BudgetTracker | None = None,
    allow_llm: bool = True,
    recursion_limit: int | None = None,
) -> ReasoningChain:
    """Run the full agentic loop and return a reasoning chain.

    When ``guard_cfg`` is omitted, limits are loaded from application config
    (P2-AG-04). ``recursion_limit`` defaults to ``guard_cfg.recursion_limit``.
    """
    guard_cfg = guard_cfg or GuardrailConfig.from_app_config()
    budget = budget or guard_cfg.budget_tracker()
    rec_limit = recursion_limit if recursion_limit is not None else guard_cfg.recursion_limit
    graph = build_graph(executor, llm, guard_cfg, budget=budget)
    chain = ReasoningChain(question=question, route="agentic")
    t0 = time.perf_counter()
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
        config={"recursion_limit": rec_limit},
    )
    out = ReasoningChain.model_validate(result["chain"])
    out.cost.latency_ms = int((time.perf_counter() - t0) * 1000)
    if budget:
        snap = budget.snapshot()
        out.cost.llm_calls = snap["llm_calls"]
        out.cost.tokens = snap["total_tokens"]
        out.cost.prompt_tokens = snap["prompt_tokens"]
        out.cost.completion_tokens = snap["completion_tokens"]
    return out
