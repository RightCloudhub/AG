"""LangGraph plumbing for the Deep strategy: initial state + stream → events."""

from __future__ import annotations

import time
from collections.abc import Generator, Iterator
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from langgraph.errors import GraphRecursionError
from langgraph.types import Overwrite, StreamMode

from agentic_graphrag.agent.graph_types import AgentGraph, RunnableConfig
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import build_graph, finalize_agentic_chain, invoke_config
from agentic_graphrag.agent.loop_stream_events import EVENT_FINAL_CHAIN, events_for_node
from agentic_graphrag.agent.state import AgentState, initial_state
from agentic_graphrag.core.enums import GraphStreamMode, QueryRoute
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ReasoningChain

_STREAM_MODES: list[StreamMode] = [GraphStreamMode.UPDATES.value, GraphStreamMode.VALUES.value]


def _initial_state(
    question: str,
    chain: ReasoningChain,
    *,
    allow_llm: bool,
    tenant_id: str = "",
    seed_evidence: list[Candidate] | None = None,
) -> AgentState:
    """Initial agent state via the single constructor in :mod:`agent.state`."""
    return initial_state(
        question,
        chain,
        allow_llm=allow_llm,
        tenant_id=tenant_id,
        evidence=seed_evidence,
    )


def _stream_graph_updates(
    graph: AgentGraph,
    initial: AgentState | None,
    config: RunnableConfig,
) -> Generator[tuple[str, object], None, AgentState | None]:
    """Yield hop events; return full accumulated state from ``values`` mode."""
    final_state: AgentState | None = None
    # Fresh queries reset append channels even when the caller reuses a thread.
    graph_input = (
        None
        if initial is None
        else cast(
            AgentState,
            {
                **initial,
                "evidence": Overwrite(initial["evidence"]),
                "register_runtime": Overwrite(initial["register_runtime"]),
                "proposals": Overwrite(initial["proposals"]),
                "events": Overwrite(initial["events"]),
                "budget_ledger": Overwrite(initial["budget_ledger"]),
                "query_cache": Overwrite(initial["query_cache"]),
            },
        )
    )
    for item in graph.stream(graph_input, config=config, stream_mode=_STREAM_MODES):
        mode, chunk = _unpack_stream_item(item)
        if mode == GraphStreamMode.VALUES and isinstance(chunk, dict):
            final_state = cast(AgentState, chunk)
            continue
        if mode != GraphStreamMode.UPDATES or not isinstance(chunk, dict):
            continue
        for node_name, delta in cast(dict[str, AgentState], chunk).items():
            if isinstance(delta, dict):
                yield from events_for_node(node_name, delta)
    return final_state


def _unpack_stream_item(item: object) -> tuple[str, object]:
    """Normalize multi-mode stream items to (mode, chunk)."""
    if isinstance(item, tuple) and len(item) == 2:
        return str(item[0]), item[1]
    # Single-mode fallback: treat bare dict as updates
    return GraphStreamMode.UPDATES, item


if TYPE_CHECKING:
    from agentic_graphrag.agent.pipeline import _StreamCtx


def _iter_agentic(
    ctx: _StreamCtx, *, seed_evidence: list[Candidate] | None = None
) -> Iterator[tuple[str, object]]:
    from agentic_graphrag.config import get_config

    opts = ctx.run_opts
    agent_config = opts.agent_config or get_config().agent
    if agent_config.mode == "multi" and opts.allow_llm and ctx.llm is not None:
        from agentic_graphrag.agent.pipeline_multi import iter_multi

        yield from iter_multi(ctx, agent_config, seed_evidence=seed_evidence)
        return
    yield from _iter_single_agentic(ctx, seed_evidence=seed_evidence)


def _iter_single_agentic(
    ctx: _StreamCtx, *, seed_evidence: list[Candidate] | None = None
) -> Iterator[tuple[str, object]]:
    from agentic_graphrag.agent.pipeline import AgentStreamEmptyError

    opts = ctx.run_opts
    guard_cfg = opts.guard_cfg or GuardrailConfig.from_app_config()
    budget = opts.budget or guard_cfg.budget_tracker()
    rec_limit = (
        opts.recursion_limit if opts.recursion_limit is not None else guard_cfg.recursion_limit
    )
    chain = ReasoningChain(
        question=ctx.question,
        route=QueryRoute.AGENTIC,
        query_id=ctx.opts.query_id or str(uuid4()),
    )
    tid = opts.thread_id or chain.query_id or str(uuid4())
    graph = build_graph(
        ctx.executor, ctx.llm, guard_cfg, budget=budget, checkpointer=opts.checkpointer
    )
    t0 = time.perf_counter()
    initial = _initial_state(
        ctx.question,
        chain,
        allow_llm=opts.allow_llm,
        tenant_id=opts.tenant_id or "",
        seed_evidence=seed_evidence,
    )
    config = invoke_config(tid, recursion_limit=rec_limit)
    try:
        final_state = yield from _stream_graph_updates(graph, initial, config)
    except GraphRecursionError:
        from agentic_graphrag.agent.loop_recover import recover_chain_after_recursion

        recovered = recover_chain_after_recursion(
            graph,
            thread_id=tid,
            question=ctx.question,
            budget=budget,
            t0=t0,
            llm=ctx.llm,
            allow_llm=opts.allow_llm,
        )
        yield EVENT_FINAL_CHAIN, recovered
        return
    if final_state is None:
        raise AgentStreamEmptyError("agent graph stream produced no state")
    yield EVENT_FINAL_CHAIN, finalize_agentic_chain(final_state, budget=budget, tid=tid, t0=t0)
