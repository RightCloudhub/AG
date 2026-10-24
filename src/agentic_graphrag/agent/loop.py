"""LangGraph StateGraph agent loop assembly (ADR-005, FR-AG-02~07).

Node handlers live in :mod:`agentic_graphrag.agent.loop_runtime`.
This module wires the StateGraph, attaches a checkpointer (P2-AG-03),
and exposes :func:`run_agentic_query` / :func:`run_query` (with triage).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from agentic_graphrag.agent.checkpointer import default_checkpointer
from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop_policy import should_escalate_chain
from agentic_graphrag.agent.loop_runtime import AgentRuntime
from agentic_graphrag.agent.options import AgentDeps, AgentRunOptions, QueryOptions
from agentic_graphrag.agent.state import AgentState
from agentic_graphrag.core.constants import DEFAULT_RECURSION_LIMIT, MILLISECONDS_PER_SECOND
from agentic_graphrag.core.enums import AgentNode
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.generation.confidence import grade_confidence
from agentic_graphrag.governance.budget import BudgetTracker

_MS_PER_SEC = MILLISECONDS_PER_SECOND

if TYPE_CHECKING:
    from agentic_graphrag.agent.graph_types import AgentCheckpointer, AgentGraph, RunnableConfig

__all__ = [
    "AgentDeps",
    "AgentRunOptions",
    "AgentState",
    "QueryOptions",
    "build_graph",
    "finalize_agentic_chain",
    "invoke_config",
    "resolved_run_opts",
    "run_agentic_query",
    "run_query",
    "should_escalate_chain",
]


def build_graph(
    executor: Executor,
    llm: LLMClient | None,
    guard_cfg: GuardrailConfig,
    *,
    budget: BudgetTracker | None = None,
    checkpointer: AgentCheckpointer | None = None,
    deps: AgentDeps | None = None,
) -> AgentGraph:
    """Compile StateGraph with checkpointer for durable agent state."""
    from langgraph.graph import END, StateGraph

    if deps is None:
        deps = AgentDeps(executor, llm, guard_cfg, budget=budget)
    rt = AgentRuntime(deps.executor, deps.llm, deps.guard_cfg, budget=deps.budget)
    cp = checkpointer if checkpointer is not None else default_checkpointer()
    g: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
    g.add_node(AgentNode.PLANNER, rt.node_planner)
    g.add_node(AgentNode.EXECUTOR, rt.node_executor)
    g.add_node(AgentNode.CRITIC, rt.node_critic)
    g.add_node(AgentNode.ANSWER, rt.node_answer)
    g.set_entry_point(AgentNode.PLANNER)
    g.add_edge(AgentNode.PLANNER, AgentNode.EXECUTOR)
    # Skip critic when executor already finished (guardrail trip / empty plan).
    g.add_conditional_edges(
        AgentNode.EXECUTOR,
        rt.route_after_executor,
        {AgentNode.CRITIC: AgentNode.CRITIC, AgentNode.ANSWER: AgentNode.ANSWER},
    )
    g.add_conditional_edges(
        AgentNode.CRITIC,
        rt.route_after_critic,
        {AgentNode.EXECUTOR: AgentNode.EXECUTOR, AgentNode.ANSWER: AgentNode.ANSWER},
    )
    # §1.5: a structured evidence gap sends the answer back to retrieval
    # (bounded by max_hops and MAX_ANSWER_RETRIEVALS).
    g.add_conditional_edges(
        AgentNode.ANSWER,
        rt.route_after_answer,
        {AgentNode.EXECUTOR: AgentNode.EXECUTOR, AgentNode.END: END},
    )
    return g.compile(checkpointer=cp)


def invoke_config(
    thread_id: str,
    *,
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
) -> RunnableConfig:
    """RunnableConfig for checkpointer-backed invoke / get_state."""
    return {
        "recursion_limit": recursion_limit,
        "configurable": {"thread_id": thread_id},
    }


def run_agentic_query(
    question: str,
    executor: Executor,
    llm: LLMClient | None,
    *,
    options: AgentRunOptions | None = None,
    guard_cfg: GuardrailConfig | None = None,
    budget: BudgetTracker | None = None,
    allow_llm: bool = True,
    recursion_limit: int | None = None,
    checkpointer: AgentCheckpointer | None = None,
    thread_id: str | None = None,
    tenant_id: str = "",
) -> ReasoningChain:
    """Run the full agentic loop; keyword args or ``options`` are equivalent."""
    opts = options or AgentRunOptions(
        guard_cfg=guard_cfg,
        budget=budget,
        allow_llm=allow_llm,
        recursion_limit=recursion_limit,
        checkpointer=checkpointer,
        thread_id=thread_id,
        tenant_id=tenant_id,
    )
    return _run_agentic(question, executor, llm, opts=opts)


def run_query(
    question: str,
    executor: Executor,
    llm: LLMClient | None,
    *,
    options: QueryOptions | None = None,
    guard_cfg: GuardrailConfig | None = None,
    budget: BudgetTracker | None = None,
    allow_llm: bool = True,
    recursion_limit: int | None = None,
    checkpointer: AgentCheckpointer | None = None,
    thread_id: str | None = None,
    force_agentic: bool = False,
    enable_triage: bool = True,
    known_entities: list[str] | None = None,
    tenant_id: str = "",
) -> ReasoningChain:
    """Entry with complexity triage (Fast Path vs Agentic, P3-PERF-01)."""
    opts = options or QueryOptions(
        guard_cfg=guard_cfg,
        budget=budget,
        allow_llm=allow_llm,
        recursion_limit=recursion_limit,
        checkpointer=checkpointer,
        thread_id=thread_id,
        force_agentic=force_agentic,
        enable_triage=enable_triage,
        known_entities=known_entities,
        tenant_id=tenant_id,
    )
    from agentic_graphrag.agent.pipeline import run_pipeline

    return run_pipeline(question, executor, llm, options=opts)


def _run_agentic(
    question: str,
    executor: Executor,
    llm: LLMClient | None,
    *,
    opts: AgentRunOptions,
) -> ReasoningChain:
    """Deep strategy, non-streaming: drain the pipeline's agentic iterator."""
    from agentic_graphrag.agent.pipeline import drain, iter_agentic

    return drain(iter_agentic(question, executor, llm, run_opts=opts))


def finalize_agentic_chain(
    result: dict[str, Any],
    *,
    budget: BudgetTracker | None,
    tid: str,
    t0: float,
) -> ReasoningChain:
    out = ReasoningChain.model_validate(result["chain"])
    out.cost.latency_ms = int((time.perf_counter() - t0) * _MS_PER_SEC)
    if budget:
        snap = budget.snapshot()
        out.cost.llm_calls = snap["llm_calls"]
        out.cost.tokens = snap["total_tokens"]
        out.cost.prompt_tokens = snap["prompt_tokens"]
        out.cost.completion_tokens = snap["completion_tokens"]
    meta = dict(out.metadata or {})
    meta["thread_id"] = tid
    meta["checkpointer"] = True
    mem_snap = result.get("memory_snapshot") or {}
    meta["memory_evidence_count"] = len(mem_snap.get("evidence") or [])
    evidence = [Candidate.model_validate(e) for e in (result.get("evidence") or [])]
    meta["confidence"] = grade_confidence(out, evidence)
    out.metadata = meta
    return out


def resolved_run_opts(opts: QueryOptions) -> AgentRunOptions:
    guard_cfg = opts.guard_cfg or GuardrailConfig.from_app_config()
    budget = opts.budget or guard_cfg.budget_tracker()
    return AgentRunOptions(
        guard_cfg=guard_cfg,
        budget=budget,
        allow_llm=opts.allow_llm,
        recursion_limit=opts.recursion_limit,
        checkpointer=opts.checkpointer,
        thread_id=opts.thread_id,
        tenant_id=opts.tenant_id,
    )
