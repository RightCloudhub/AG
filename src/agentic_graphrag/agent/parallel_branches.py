"""Concurrent execution of independent DAG branches (docs/design/BUSINESS_LOGIC.md BL-12).

The planner emits a sub-question DAG, but the loop used to walk it one node per
hop: independent branches were serialized (latency Σ instead of max) and each
consumed a unit of the *depth* budget. When the executor reaches a node, every
later node that is already ready — all of its dependencies done, not depending
on the current node, no unresolved ``{from:sqN}`` slot — is retrieved
**concurrently** with it. Their results are parked in agent state under
``prefetched``; when the loop reaches such a node it consumes the parked result
at the same hop level instead of starting a new hop, so the hop counter now
measures dependency depth. Critic and answer semantics are unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from agentic_graphrag.agent.entities import extract_entity_mentions
from agentic_graphrag.agent.planner import SubQuestion
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ToolCallTrace

PREFETCH_KEY = "prefetched"
PARALLEL_GROUPS_KEY = "parallel_branches"

Retrieved = tuple[list[Candidate], list[ToolCallTrace]]


@dataclass
class BranchRun:
    """Per-call options shared by every branch of one concurrent group."""

    hints_for: Callable[[str], list[str]]
    allow_llm: bool
    tenant_id: str | None


def is_prefetched(state: dict[str, Any], sq_id: str) -> bool:
    return sq_id in (state.get(PREFETCH_KEY) or {})


def ready_siblings(
    sqs: list[dict[str, Any]], idx: int, *, done_ids: set[str], limit: int
) -> list[SubQuestion]:
    """Later plan nodes that can run now, independently of node ``idx``."""
    if limit <= 0 or idx >= len(sqs):
        return []
    current_id = str(sqs[idx].get("id"))
    out: list[SubQuestion] = []
    for raw in sqs[idx + 1 :]:
        sq = SubQuestion.model_validate(raw)
        if _runnable_now(sq, current_id, done_ids):
            out.append(sq)
        if len(out) >= limit:
            break
    return out


def _runnable_now(sq: SubQuestion, current_id: str, done_ids: set[str]) -> bool:
    if sq.id in done_ids or current_id in sq.depends_on:
        return False
    if sq.unresolved_placeholders():
        return False
    return all(dep in done_ids for dep in sq.depends_on)


def retrieve_group(
    executor: Any, jobs: list[tuple[str, str]], run: BranchRun
) -> dict[str, Retrieved]:
    """Run ``executor.run`` for every ``(sq_id, text)`` job concurrently."""
    if len(jobs) == 1:
        sq_id, text = jobs[0]
        return {sq_id: _run_one(executor, text, run)}
    with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="agr-branch") as pool:
        futures = {sq_id: pool.submit(_run_one, executor, text, run) for sq_id, text in jobs}
        return {sq_id: fut.result() for sq_id, fut in futures.items()}


def _run_one(executor: Any, text: str, run: BranchRun) -> Retrieved:
    return executor.run(
        text,
        entities_hint=run.hints_for(text),
        allow_llm=run.allow_llm,
        tenant_id=run.tenant_id,
    )


def park(store: dict[str, Any], results: dict[str, Retrieved]) -> dict[str, Any]:
    """Serialize sibling results into the (checkpointable) prefetch store."""
    out = dict(store)
    for sq_id, (cands, traces) in results.items():
        out[sq_id] = {
            "candidates": [c.model_dump() for c in cands],
            "traces": [t.model_dump() for t in traces],
        }
    return out


def take(store: dict[str, Any], sq_id: str) -> tuple[Retrieved | None, dict[str, Any]]:
    """Pop a parked result; returns ``(result_or_None, remaining_store)``."""
    remaining = dict(store)
    raw = remaining.pop(sq_id, None)
    if raw is None:
        return None, remaining
    cands = [Candidate.model_validate(c) for c in raw.get("candidates") or []]
    traces = [ToolCallTrace.model_validate(t) for t in raw.get("traces") or []]
    return (cands, traces), remaining


def retrieve_for_node(ctx: Any) -> tuple[Any, dict[str, Any], list[str]]:
    """Parked branch result, or this node plus its ready siblings concurrently (BL-12)."""
    parked, store = take(dict(ctx.state.get(PREFETCH_KEY) or {}), ctx.sq.id)
    if parked is not None:
        return parked, store, [ctx.sq.id]
    cfg = ctx.guards.config
    limit = cfg.max_parallel_branches - 1 if cfg.parallel_branches else 0
    siblings = ready_siblings(
        ctx.sqs, ctx.idx, done_ids=ctx.memory.done_subquestion_ids, limit=limit
    )
    jobs = [(ctx.sq.id, ctx.sq.text)] + [(s.id, s.text) for s in siblings]
    run = BranchRun(
        hints_for=lambda text: hints_for(ctx, text),
        allow_llm=bool(ctx.state.get("allow_llm", True)) and ctx.llm is not None,
        tenant_id=ctx.state.get("tenant_id") or None,
    )
    results = retrieve_group(ctx.executor, jobs, run)
    current = results.pop(ctx.sq.id)
    return current, park(store, results), [sq_id for sq_id, _ in jobs]


def hints_for(ctx: Any, text: str) -> list[str]:
    known = ctx.executor.known_entities or None
    hints = extract_entity_mentions(ctx.state["question"] + " " + text, known)
    for conc in ctx.memory.conclusions_by_subquestion.values():
        hints.extend(extract_entity_mentions(conc, known))
    return hints
