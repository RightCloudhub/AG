"""Multi-agent streaming/resume boundary with tenant-scoped checkpoint threads."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator
from dataclasses import replace
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from agentic_graphrag.agent.blackboard import BudgetEntry, usage_totals
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import finalize_agentic_chain, invoke_config
from agentic_graphrag.agent.loop_stream_events import EVENT_FINAL_CHAIN
from agentic_graphrag.agent.multi_graph import build_multi_graph
from agentic_graphrag.agent.options import AgentDeps
from agentic_graphrag.agent.pipeline_graph import _stream_graph_updates
from agentic_graphrag.agent.state import AgentState, initial_state
from agentic_graphrag.config_agent import AgentConfig
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.budget import BudgetTracker

if TYPE_CHECKING:
    from agentic_graphrag.agent.pipeline import _StreamCtx


def checkpoint_thread(tenant_id: str, thread_id: str) -> str:
    return "multi:" + hashlib.sha256(json.dumps([tenant_id, thread_id]).encode()).hexdigest()


def iter_multi(
    ctx: _StreamCtx, agent_config: AgentConfig, *, seed_evidence: list[Candidate] | None = None
) -> Iterator[tuple[str, object]]:
    opts = ctx.run_opts
    guards = opts.guard_cfg or GuardrailConfig.from_app_config()
    budget = opts.budget or guards.budget_tracker()
    guards = replace(
        guards,
        max_tokens=min(guards.max_tokens, budget.max_tokens),
        max_llm_calls=min(guards.max_llm_calls, budget.max_llm_calls),
    )
    graph = build_multi_graph(
        AgentDeps(ctx.executor, ctx.llm, guards, budget),
        agent_config,
        checkpointer=opts.checkpointer,
    )
    thread_id = opts.thread_id or ctx.opts.query_id or str(uuid4())
    config = invoke_config(
        checkpoint_thread(opts.tenant_id, thread_id),
        recursion_limit=opts.recursion_limit or 4 * guards.max_hops + 7,
    )
    initial: AgentState | None = _initial(ctx, guards, budget=budget, evidence=seed_evidence)
    if opts.resume:
        _validate_resume(ctx, cast(AgentState, graph.get_state(config).values))
        initial = None
    started = time.perf_counter()
    final = yield from _stream_graph_updates(graph, initial, config)
    if final is None:
        raise RuntimeError("multi-agent graph produced no state")
    totals = usage_totals(final["budget_ledger"])
    budget.llm_calls = totals["llm_calls"]
    budget.prompt_tokens = totals["prompt_tokens"]
    budget.completion_tokens = totals["completion_tokens"]
    yield EVENT_FINAL_CHAIN, finalize_agentic_chain(final, budget=budget, tid=thread_id, t0=started)


def _initial(
    ctx: _StreamCtx,
    guards: GuardrailConfig,
    *,
    budget: BudgetTracker,
    evidence: list[Candidate] | None,
) -> AgentState:
    opts = ctx.run_opts
    chain = ReasoningChain(question=ctx.question, query_id=ctx.opts.query_id or str(uuid4()))
    initial = initial_state(
        ctx.question, chain, allow_llm=True, tenant_id=opts.tenant_id, evidence=evidence
    )
    initial["deadline"] = (
        time.time() + guards.query_timeout_seconds if guards.query_timeout_seconds else 0
    )
    initial["budget_ledger"] = [
        BudgetEntry(
            entry_id=f"{chain.query_id}:entry",
            branch_id="entry",
            run_id=chain.query_id,
            tenant_id=opts.tenant_id,
            req_id="triage",
            role="triage",
            calls=budget.llm_calls,
            prompt_tokens=budget.prompt_tokens,
            completion_tokens=budget.completion_tokens,
        )
    ]
    return initial


def _validate_resume(ctx: _StreamCtx, saved: AgentState) -> None:
    opts = ctx.run_opts
    if not opts.thread_id or not saved:
        raise ValueError("resume requires an existing thread_id and checkpointer")
    if saved.get("tenant_id") != opts.tenant_id or saved.get("question") != ctx.question:
        raise ValueError("checkpoint identity does not match the requested run")
