"""QueryPipeline: the single execution path for every query (refactor §1.1).

Router (chitchat → triage) picks Direct / Fast / Deep and yields execution
events; the terminal event is always ``(EVENT_FINAL_CHAIN, chain)``. SSE
consumes the events; non-streaming (:func:`run_pipeline`) drains the same
iterator. Fast → Deep escalation carries the fast path's evidence forward.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from agentic_graphrag.agent.chitchat import try_chitchat_answer
from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.fast_path import run_fast_path_with_evidence
from agentic_graphrag.agent.loop import (
    resolved_run_opts,
    should_escalate_chain,
)
from agentic_graphrag.agent.loop_stream_events import (
    EVENT_FINAL_CHAIN,
    EVENT_HOP_DONE,
    EVENT_SUB_QUESTION,
    EVENT_THINKING,
    EVENT_TRIAGE,
    fast_path_step_events,
)
from agentic_graphrag.agent.options import AgentRunOptions, QueryOptions
from agentic_graphrag.agent.pipeline_graph import _initial_state as _initial_state
from agentic_graphrag.agent.pipeline_graph import _iter_agentic
from agentic_graphrag.agent.triage import Route, TriageResult, triage
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.prompts import prompt_versions, reset_prompt_versions

# Re-export SSE event names for callers that import from this module.
__all__ = [
    "EVENT_FINAL_CHAIN",
    "EVENT_HOP_DONE",
    "EVENT_SUB_QUESTION",
    "EVENT_THINKING",
    "EVENT_TRIAGE",
    "AgentStreamEmptyError",
    "iter_query_progress",
    "run_pipeline",
]

DEFAULT_ESTIMATED_HOPS = 2


class AgentStreamEmptyError(RuntimeError):
    """Raised when LangGraph stream yields no usable state (fail closed)."""


@dataclass(frozen=True)
class _StreamCtx:
    """Bundles question + runtime deps for stream helpers (param-limit hygiene)."""

    question: str
    executor: Executor
    llm: LLMClient | None
    opts: QueryOptions
    run_opts: AgentRunOptions


def iter_query_progress(
    question: str,
    executor: Executor,
    llm: LLMClient | None,
    *,
    options: QueryOptions | None = None,
) -> Iterator[tuple[str, Any]]:
    """Yield (event, payload); terminal event is ``(EVENT_FINAL_CHAIN, chain)``.

    The final chain carries ``metadata.prompt_versions`` (refactor §5.4).
    """
    reset_prompt_versions()
    for etype, payload in _route(question, executor, llm, options=options):
        if etype == EVENT_FINAL_CHAIN and isinstance(payload, ReasoningChain):
            used = prompt_versions()
            if used:
                payload.metadata = {**(payload.metadata or {}), "prompt_versions": used}
        yield etype, payload


def _route(
    question: str,
    executor: Executor,
    llm: LLMClient | None,
    *,
    options: QueryOptions | None = None,
) -> Iterator[tuple[str, Any]]:
    """Router: chitchat (Direct) → triage → Fast or Deep strategy."""
    opts = options or QueryOptions()
    chitchat = try_chitchat_answer(question)
    if chitchat is not None:
        yield (
            EVENT_TRIAGE,
            {
                "route": "chitchat",
                "rationale": "greeting_or_capability",
                "estimated_hops": 0,
                "confidence": 1.0,
                "rule_hit": "chitchat",
            },
        )
        yield EVENT_FINAL_CHAIN, chitchat
        return
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


def run_pipeline(
    question: str,
    executor: Executor,
    llm: LLMClient | None,
    *,
    options: QueryOptions | None = None,
) -> ReasoningChain:
    """Non-streaming entry: drain :func:`iter_query_progress` to its final chain."""
    return drain(iter_query_progress(question, executor, llm, options=options))


def drain(events: Iterator[tuple[str, Any]]) -> ReasoningChain:
    final: ReasoningChain | None = None
    for etype, payload in events:
        if etype == EVENT_FINAL_CHAIN:
            final = payload
    if final is None:
        raise AgentStreamEmptyError("query pipeline produced no final chain")
    return final


def iter_agentic(
    question: str,
    executor: Executor,
    llm: LLMClient | None,
    *,
    run_opts: AgentRunOptions,
) -> Iterator[tuple[str, Any]]:
    """Deep strategy only (no router), for callers that force the agentic loop."""
    ctx = _StreamCtx(
        question=question,
        executor=executor,
        llm=llm,
        opts=QueryOptions(),
        run_opts=run_opts,
    )
    yield from _iter_agentic(ctx)


def _stream_force_or_no_triage(ctx: _StreamCtx) -> Iterator[tuple[str, Any]]:
    # Contract stability: clients always see a triage frame first — including
    # when triage is disabled, which previously emitted no frame at all (BL-11).
    yield EVENT_TRIAGE, _entry_triage(force_agentic=ctx.opts.force_agentic).model_dump(mode="json")
    for etype, payload in _iter_agentic(ctx):
        if etype == EVENT_FINAL_CHAIN and ctx.opts.force_agentic:
            chain: ReasoningChain = payload
            chain.metadata = {**(chain.metadata or {}), "force_agentic": True}
            yield EVENT_FINAL_CHAIN, chain
        else:
            yield etype, payload


def _entry_triage(*, force_agentic: bool) -> TriageResult:
    rule = "force_agentic" if force_agentic else "triage_disabled"
    return TriageResult(
        route=Route.AGENTIC,
        rationale=rule,
        estimated_hops=DEFAULT_ESTIMATED_HOPS,
        confidence=1.0,
        rule_hit=rule,
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
    seed_evidence: list[Candidate] | None = None,
) -> Iterator[tuple[str, Any]]:
    for etype, payload in _iter_agentic(ctx, seed_evidence=seed_evidence):
        if etype != EVENT_FINAL_CHAIN:
            yield etype, payload
            continue
        chain: ReasoningChain = payload
        meta = {**(chain.metadata or {}), "triage": triage_meta}
        if escalated:
            meta["escalated_from_fast_path"] = True
            meta["inherited_evidence"] = len(seed_evidence or [])
        chain.metadata = meta
        yield EVENT_FINAL_CHAIN, chain


def _iter_fast_or_escalate(
    ctx: _StreamCtx, triage_meta: dict[str, Any]
) -> Iterator[tuple[str, Any]]:
    chain, candidates = run_fast_path_with_evidence(
        ctx.question,
        ctx.executor,
        ctx.llm,
        allow_llm=ctx.run_opts.allow_llm,
        budget=ctx.run_opts.budget,
        triage_meta=triage_meta,
        tenant_id=ctx.run_opts.tenant_id or None,
    )
    if not should_escalate_chain(chain):
        yield from _emit_steps_then_chain(chain)
        return
    yield from _agentic_with_meta(ctx, triage_meta, escalated=True, seed_evidence=candidates)


def _emit_steps_then_chain(chain: ReasoningChain) -> Iterator[tuple[str, Any]]:
    yield from fast_path_step_events(list(chain.steps))
    yield EVENT_FINAL_CHAIN, chain
