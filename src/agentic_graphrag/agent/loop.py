"""LangGraph StateGraph agent loop assembly (ADR-005, FR-AG-02~07).

Node handlers live in :mod:`agentic_graphrag.agent.loop_runtime`.
This module wires the StateGraph, attaches a checkpointer (P2-AG-03),
and exposes :func:`run_agentic_query` / :func:`run_query` (with triage).
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from agentic_graphrag.agent.checkpointer import default_checkpointer
from agentic_graphrag.agent.chitchat import try_chitchat_answer
from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.fast_path import run_fast_path
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop_runtime import AgentRuntime, AgentState
from agentic_graphrag.agent.options import AgentDeps, AgentRunOptions, QueryOptions
from agentic_graphrag.agent.triage import Route, should_escalate_fast_path, triage
from agentic_graphrag.generation.confidence import grade_confidence
from agentic_graphrag.generation.trace import ReasoningChain
from agentic_graphrag.llm.budget import BudgetTracker
from agentic_graphrag.llm.provider import LLMProvider
from agentic_graphrag.retrieval.contracts import Candidate

_MS_PER_SEC = 1000

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
    llm: LLMProvider | None,
    guard_cfg: GuardrailConfig,
    *,
    budget: BudgetTracker | None = None,
    checkpointer: Any | None = None,
    deps: AgentDeps | None = None,
):
    """Compile StateGraph with checkpointer for durable agent state."""
    from langgraph.graph import END, StateGraph

    if deps is None:
        deps = AgentDeps(executor, llm, guard_cfg, budget=budget)
    rt = AgentRuntime(deps.executor, deps.llm, deps.guard_cfg, budget=deps.budget)
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
    options: AgentRunOptions | None = None,
    guard_cfg: GuardrailConfig | None = None,
    budget: BudgetTracker | None = None,
    allow_llm: bool = True,
    recursion_limit: int | None = None,
    checkpointer: Any | None = None,
    thread_id: str | None = None,
) -> ReasoningChain:
    """Run the full agentic loop; keyword args or ``options`` are equivalent."""
    opts = options or AgentRunOptions(
        guard_cfg=guard_cfg,
        budget=budget,
        allow_llm=allow_llm,
        recursion_limit=recursion_limit,
        checkpointer=checkpointer,
        thread_id=thread_id,
    )
    return _run_agentic(question, executor, llm, opts=opts)


def run_query(
    question: str,
    executor: Executor,
    llm: LLMProvider | None,
    *,
    options: QueryOptions | None = None,
    guard_cfg: GuardrailConfig | None = None,
    budget: BudgetTracker | None = None,
    allow_llm: bool = True,
    recursion_limit: int | None = None,
    checkpointer: Any | None = None,
    thread_id: str | None = None,
    force_agentic: bool = False,
    enable_triage: bool = True,
    known_entities: list[str] | None = None,
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
    )
    return _run_with_triage(question, executor, llm, opts=opts)


def _run_agentic(
    question: str,
    executor: Executor,
    llm: LLMProvider | None,
    *,
    opts: AgentRunOptions,
) -> ReasoningChain:
    guard_cfg = opts.guard_cfg or GuardrailConfig.from_app_config()
    budget = opts.budget or guard_cfg.budget_tracker()
    rec_limit = (
        opts.recursion_limit if opts.recursion_limit is not None else guard_cfg.recursion_limit
    )
    chain = ReasoningChain(question=question, route="agentic")
    tid = opts.thread_id or chain.query_id or str(uuid4())
    graph = build_graph(executor, llm, guard_cfg, budget=budget, checkpointer=opts.checkpointer)
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
            "allow_llm": opts.allow_llm,
        },
        config=invoke_config(tid, recursion_limit=rec_limit),
    )
    return finalize_agentic_chain(result, budget=budget, tid=tid, t0=t0)


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


def _run_with_triage(
    question: str,
    executor: Executor,
    llm: LLMProvider | None,
    *,
    opts: QueryOptions,
) -> ReasoningChain:
    # Greetings / capability meta — skip retrieval entirely (even if force_agentic).
    chitchat = try_chitchat_answer(question)
    if chitchat is not None:
        return chitchat

    run_opts = resolved_run_opts(opts)
    known = (
        opts.known_entities
        if opts.known_entities is not None
        else list(executor.known_entities or [])
    )

    if not opts.enable_triage or opts.force_agentic:
        chain = _run_agentic(question, executor, llm, opts=run_opts)
        if opts.force_agentic:
            chain.metadata = {**(chain.metadata or {}), "force_agentic": True}
        return chain

    decision = triage(
        question,
        llm if opts.allow_llm else None,
        allow_llm=opts.allow_llm and llm is not None,
        force_agentic=False,
        known_entities=known,
    )
    triage_meta = decision.model_dump(mode="json")
    if decision.route == Route.FAST_PATH:
        return _fast_path_or_escalate(
            question, executor, llm, opts=run_opts, triage_meta=triage_meta
        )
    chain = _run_agentic(question, executor, llm, opts=run_opts)
    chain.metadata = {**(chain.metadata or {}), "triage": triage_meta}
    return chain


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
    )


def _fast_path_or_escalate(
    question: str,
    executor: Executor,
    llm: LLMProvider | None,
    *,
    opts: AgentRunOptions,
    triage_meta: dict[str, Any],
) -> ReasoningChain:
    chain = run_fast_path(
        question,
        executor,
        llm,
        allow_llm=opts.allow_llm,
        budget=opts.budget,
        triage_meta=triage_meta,
    )
    if not should_escalate_chain(chain):
        return chain
    agentic = _run_agentic(question, executor, llm, opts=opts)
    agentic.metadata = {
        **(agentic.metadata or {}),
        "triage": triage_meta,
        "escalated_from_fast_path": True,
    }
    return agentic


def should_escalate_chain(chain: ReasoningChain) -> bool:
    ev_count = sum(len(s.evidence_ids) for s in chain.steps)
    has_graph = any("graph" in (tc.tool or "") for s in chain.steps for tc in s.tool_calls)
    return should_escalate_fast_path(
        ev_count,
        has_graph=has_graph,
        answer_status=chain.status.value if chain.status else None,
    )
