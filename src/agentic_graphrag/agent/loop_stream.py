"""Live progress streaming for the agent loop (P3-PERF-06 true incremental SSE).

Maps LangGraph ``graph.stream(..., stream_mode=["updates","values"])`` to the
SSE event contract (``sub_question`` / ``hop_done``). Sync equivalent of the
design-doc ``astream_events`` mapping — yields as each node completes.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.fast_path import run_fast_path
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import (
    build_graph,
    finalize_agentic_chain,
    invoke_config,
    resolved_run_opts,
    should_escalate_chain,
)
from agentic_graphrag.agent.options import AgentRunOptions, QueryOptions
from agentic_graphrag.agent.triage import Route, TriageResult, triage
from agentic_graphrag.generation.trace import ReasoningChain
from agentic_graphrag.llm.provider import LLMProvider

EVENT_FINAL_CHAIN = "__final_chain__"
EVENT_SUB_QUESTION = "sub_question"
EVENT_HOP_DONE = "hop_done"
EVENT_TRIAGE = "triage"

_STREAM_MODES = ["updates", "values"]


class AgentStreamEmptyError(RuntimeError):
    """Raised when LangGraph stream yields no usable state (fail closed)."""


@dataclass(frozen=True)
class _StreamCtx:
    """Bundles question + runtime deps for stream helpers (param-limit hygiene)."""

    question: str
    executor: Executor
    llm: LLMProvider | None
    opts: QueryOptions
    run_opts: AgentRunOptions


def iter_query_progress(
    question: str,
    executor: Executor,
    llm: LLMProvider | None,
    *,
    options: QueryOptions | None = None,
) -> Iterator[tuple[str, Any]]:
    """Yield (event, payload); terminal event is ``(EVENT_FINAL_CHAIN, chain)``."""
    opts = options or QueryOptions()
    ctx = _StreamCtx(
        question=question,
        executor=executor,
        llm=llm,
        opts=opts,
        run_opts=resolved_run_opts(opts),
    )
    if not opts.enable_triage or opts.force_agentic:
        yield from _stream_force_or_no_triage(ctx)
        return
    yield from _stream_with_triage(ctx)


def _stream_force_or_no_triage(ctx: _StreamCtx) -> Iterator[tuple[str, Any]]:
    # Contract stability: clients always see a triage frame first.
    if ctx.opts.force_agentic:
        yield EVENT_TRIAGE, _force_agentic_triage().model_dump(mode="json")
    for etype, payload in _iter_agentic(ctx):
        if etype == EVENT_FINAL_CHAIN and ctx.opts.force_agentic:
            chain: ReasoningChain = payload
            chain.metadata = {**(chain.metadata or {}), "force_agentic": True}
            yield EVENT_FINAL_CHAIN, chain
        else:
            yield etype, payload


def _force_agentic_triage() -> TriageResult:
    return TriageResult(
        route=Route.AGENTIC,
        rationale="force_agentic",
        estimated_hops=2,
        confidence=1.0,
        rule_hit="force_agentic",
    )


def _stream_with_triage(ctx: _StreamCtx) -> Iterator[tuple[str, Any]]:
    known = (
        ctx.opts.known_entities
        if ctx.opts.known_entities is not None
        else list(ctx.executor.known_entities or [])
    )
    decision = triage(
        ctx.question,
        ctx.llm if ctx.opts.allow_llm else None,
        allow_llm=ctx.opts.allow_llm and ctx.llm is not None,
        force_agentic=False,
        known_entities=known,
    )
    triage_meta = decision.model_dump(mode="json")
    yield EVENT_TRIAGE, triage_meta
    if decision.route == Route.FAST_PATH:
        yield from _iter_fast_or_escalate(ctx, triage_meta)
        return
    yield from _agentic_with_meta(ctx, triage_meta)


def _agentic_with_meta(
    ctx: _StreamCtx,
    triage_meta: dict[str, Any],
    *,
    escalated: bool = False,
) -> Iterator[tuple[str, Any]]:
    for etype, payload in _iter_agentic(ctx):
        if etype != EVENT_FINAL_CHAIN:
            yield etype, payload
            continue
        chain: ReasoningChain = payload
        meta = {**(chain.metadata or {}), "triage": triage_meta}
        if escalated:
            meta["escalated_from_fast_path"] = True
        chain.metadata = meta
        yield EVENT_FINAL_CHAIN, chain


def _iter_fast_or_escalate(
    ctx: _StreamCtx, triage_meta: dict[str, Any]
) -> Iterator[tuple[str, Any]]:
    chain = run_fast_path(
        ctx.question,
        ctx.executor,
        ctx.llm,
        allow_llm=ctx.run_opts.allow_llm,
        budget=ctx.run_opts.budget,
        triage_meta=triage_meta,
    )
    if not should_escalate_chain(chain):
        yield from _emit_steps_then_chain(chain)
        return
    yield from _agentic_with_meta(ctx, triage_meta, escalated=True)


def _emit_steps_then_chain(chain: ReasoningChain) -> Iterator[tuple[str, Any]]:
    for step in chain.steps:
        yield (
            EVENT_SUB_QUESTION,
            {
                "hop": step.hop,
                "sub_question": step.sub_question,
            },
        )
        yield (
            EVENT_HOP_DONE,
            {
                "hop": step.hop,
                "conclusion": step.conclusion,
                "critic_action": step.critic_action,
            },
        )
    yield EVENT_FINAL_CHAIN, chain


def _iter_agentic(ctx: _StreamCtx) -> Iterator[tuple[str, Any]]:
    opts = ctx.run_opts
    guard_cfg = opts.guard_cfg or GuardrailConfig.from_app_config()
    budget = opts.budget or guard_cfg.budget_tracker()
    rec_limit = (
        opts.recursion_limit if opts.recursion_limit is not None else guard_cfg.recursion_limit
    )
    chain = ReasoningChain(question=ctx.question, route="agentic")
    tid = opts.thread_id or chain.query_id or str(uuid4())
    graph = build_graph(
        ctx.executor, ctx.llm, guard_cfg, budget=budget, checkpointer=opts.checkpointer
    )
    t0 = time.perf_counter()
    initial = _initial_state(ctx.question, chain, opts.allow_llm)
    config = invoke_config(tid, recursion_limit=rec_limit)
    final_state = yield from _stream_graph_updates(graph, initial, config)
    if final_state is None:
        raise AgentStreamEmptyError("agent graph stream produced no state")
    out = finalize_agentic_chain(final_state, budget=budget, tid=tid, t0=t0)
    yield EVENT_FINAL_CHAIN, out


def _initial_state(question: str, chain: ReasoningChain, allow_llm: bool) -> dict[str, Any]:
    return {
        "question": question,
        "chain": chain.model_dump(),
        "sub_questions": [],
        "current_index": 0,
        "hop": 0,
        "evidence": [],
        "done": False,
        "allow_llm": allow_llm,
    }


def _stream_graph_updates(
    graph: Any,
    initial: dict[str, Any],
    config: dict[str, Any],
) -> Iterator[tuple[str, Any]]:
    """Yield hop events; return full accumulated state from ``values`` mode."""
    final_state: dict[str, Any] | None = None
    for item in graph.stream(initial, config=config, stream_mode=_STREAM_MODES):
        mode, chunk = _unpack_stream_item(item)
        if mode == "values" and isinstance(chunk, dict):
            final_state = chunk
            continue
        if mode != "updates" or not isinstance(chunk, dict):
            continue
        for node_name, delta in chunk.items():
            if isinstance(delta, dict):
                yield from _events_for_node(node_name, delta)
    return final_state  # type: ignore[misc]


def _unpack_stream_item(item: Any) -> tuple[str, Any]:
    """Normalize multi-mode stream items to (mode, chunk)."""
    if isinstance(item, tuple) and len(item) == 2:
        return str(item[0]), item[1]
    # Single-mode fallback: treat bare dict as updates
    return "updates", item


def _events_for_node(node_name: str, delta: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    last = _last_step(delta.get("chain"))
    if last is None:
        return
    hop = last.get("hop", 0)
    if node_name == "executor":
        yield (
            EVENT_SUB_QUESTION,
            {
                "hop": hop,
                "sub_question": last.get("sub_question") or "",
            },
        )
    elif node_name == "critic":
        yield (
            EVENT_HOP_DONE,
            {
                "hop": hop,
                "conclusion": last.get("conclusion") or "",
                "critic_action": last.get("critic_action") or "",
            },
        )


def _last_step(chain_data: Any) -> dict[str, Any] | None:
    if not isinstance(chain_data, dict):
        return None
    steps = chain_data.get("steps") or []
    if not steps or not isinstance(steps[-1], dict):
        return None
    return steps[-1]
