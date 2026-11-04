"""Role attribution for routing and fast-path work before the blackboard starts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from agentic_graphrag.agent.blackboard import BudgetEntry, Role
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.llm.roles import model_name, role_client, usage_cost
from agentic_graphrag.llm.scoped import ScopedFactory, scoped_client

if TYPE_CHECKING:
    from agentic_graphrag.agent.pipeline import _StreamCtx


def multi_enabled(ctx: _StreamCtx) -> bool:
    from agentic_graphrag.config import get_config

    config = ctx.run_opts.agent_config or get_config().agent
    return config.mode == "multi" and ctx.run_opts.allow_llm and ctx.llm is not None


def entry_client(ctx: _StreamCtx, role: Role) -> LLMClient | None:
    if ctx.llm is None or not multi_enabled(ctx):
        return ctx.llm
    client = role_client(ctx.llm, role)
    budget = ctx.run_opts.budget
    if budget and isinstance(client, ScopedFactory):
        from agentic_graphrag.config import get_config

        config = ctx.run_opts.agent_config or get_config().agent
        client = scoped_client(client, budget, max_output_tokens=config.max_output_tokens)
    return client


@contextmanager
def fast_limit(ctx: _StreamCtx) -> Iterator[None]:
    """Apply the small tier for fast work; restore run ceilings before escalation."""
    budget = ctx.run_opts.budget
    if not multi_enabled(ctx) or budget is None:
        yield
        return
    from agentic_graphrag.config import get_config

    limits = (ctx.run_opts.agent_config or get_config().agent).budget_tiers.small
    tokens, calls = budget.max_tokens, budget.max_llm_calls
    budget.max_tokens, budget.max_llm_calls = min(tokens, limits.tokens), min(calls, limits.calls)
    try:
        yield
    finally:
        budget.max_tokens, budget.max_llm_calls = tokens, calls


@contextmanager
def entry_meter(ctx: _StreamCtx, role: Role, llm: LLMClient | None) -> Iterator[None]:
    budget = ctx.run_opts.budget
    before = budget.snapshot() if budget else {}
    yield
    if budget is None or llm is None or not ctx.run_opts.allow_llm:
        return
    prompt = budget.prompt_tokens - before["prompt_tokens"]
    completion = budget.completion_tokens - before["completion_tokens"]
    calls = budget.llm_calls - before["llm_calls"]
    if not (prompt or completion or calls):
        return
    ctx.entry_usage.append(
        BudgetEntry(
            entry_id=f"entry:{len(ctx.entry_usage)}",
            branch_id=f"entry:{role}",
            run_id=ctx.opts.query_id or "",
            tenant_id=ctx.run_opts.tenant_id,
            req_id=f"entry:{role}",
            role=role,
            model=model_name(llm),
            calls=calls,
            embed_calls=budget.embed_calls - before["embed_calls"],
            prompt_tokens=prompt,
            completion_tokens=completion,
            cost_usd=usage_cost(llm, prompt, completion)
            if budget.embed_calls == before["embed_calls"]
            else None,
        )
    )
