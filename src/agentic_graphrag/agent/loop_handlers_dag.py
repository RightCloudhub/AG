"""DAG-aware handler variants for the agent loop (BL-12).

Extracted from ``loop_handlers.py`` to keep that file under the 300-line
hard gate (``scripts/check_code_metrics.py``).  Every function here is a
DAG-aware counterpart of a function in ``loop_handlers`` — the non-DAG
originals remain there for the non-streaming, linear-index path.
"""

from __future__ import annotations

from typing import Any

from agentic_graphrag.agent.critic import CriticAction
from agentic_graphrag.agent.loop_handlers import (
    AgentState,
    CriticApplyCtx,
    ExecutorNodeCtx,
    _base_state_after_critic,
    _cap_hops,
)
from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.plan_dag import ready_subquestions
from agentic_graphrag.agent.planner import SubQuestion, materialize_subquestion


def skip_excluded_or_duplicate_dag(
    ctx: ExecutorNodeCtx,
    done_ids: set[str],
) -> AgentState | None:
    """BL-12: DAG-aware skip — marks excluded/duplicate node as done and retries."""
    sq = ctx.sq
    memory, guards, state = ctx.memory, ctx.guards, ctx.state
    hop = guards.state.hop
    hop_cap = hop >= guards.config.max_hops
    if memory.is_excluded(sq.text):
        memory.mark_subquestion_done(sq.id)
        done_ids.add(sq.id)
        return {
            **state,
            "sub_questions": ctx.sqs,
            "done_ids": list(done_ids),
            "hop": hop,
            "done": hop_cap or all_sq_done(ctx.sqs, done_ids),
            "guardrail_status": guards.status_text(),
            "memory_snapshot": memory.to_snapshot(),
        }
    if memory.is_duplicate_subquestion(sq.text) and ctx.idx > 0:
        memory.exclude_hypothesis(sq.text)
        done_ids.add(sq.id)
        return {
            **state,
            "sub_questions": ctx.sqs,
            "done_ids": list(done_ids),
            "hop": hop,
            "guardrail_status": guards.status_text(),
            "done": hop_cap or all_sq_done(ctx.sqs, done_ids),
            "memory_snapshot": memory.to_snapshot(),
        }
    return None


def all_sq_done(sqs: list[dict[str, Any]], done_ids: set[str]) -> bool:
    """True when every sub-question id is in done_ids."""
    return all(s.get("id") in done_ids for s in sqs)


def materialize_current_dag(
    sqs: list[dict[str, Any]],
    done_ids: set[str],
    memory: MemoryState,
) -> tuple[SubQuestion, list[dict[str, Any]], int] | None:
    """BL-12: Find the next ready DAG node and materialize it.

    Returns ``(sq, updated_sqs, index)`` or ``None`` if no pending node
    has all dependencies satisfied (all done or cyclic deadlock).
    """
    sub_questions = [SubQuestion.model_validate(s) for s in sqs]
    ready = ready_subquestions(sub_questions, done_ids)
    if not ready:
        return None
    first_ready = ready[0]
    idx = next((i for i, s in enumerate(sqs) if s.get("id") == first_ready.id), -1)
    if idx == -1:
        return None
    sq = materialize_subquestion(first_ready, memory.conclusions_by_subquestion)
    sqs = list(sqs)
    sqs[idx] = sq.model_dump()
    return sq, sqs, idx


def resolve_terminal_action_dag(
    new_state: AgentState,
    ctx: CriticApplyCtx,
    sqs: list[dict[str, Any]],
    done_ids: set[str],
) -> None:
    """BL-12: DAG-aware terminal resolution — uses done_ids instead of current_index."""
    result, memory, guards = ctx.result, ctx.memory, ctx.guards
    action = result.action
    if action == CriticAction.SUFFICIENT:
        new_state["done_ids"] = list(done_ids)
        new_state["done"] = True
        return
    if action == CriticAction.GIVE_UP or guards.state.tripped:
        if action == CriticAction.GIVE_UP:
            memory.exclude_hypothesis(ctx.sq_text)
        new_state["done_ids"] = list(done_ids)
        new_state["done"] = True
        return
    if action in (CriticAction.NEXT_HOP, CriticAction.REWRITE):
        _append_dynamic_subquestion_dag(new_state, ctx, sqs, done_ids)
        return
    # Fallthrough: mark as done and check if all nodes are finished
    done_ids.add(ctx.sq_id)
    new_state["done_ids"] = list(done_ids)
    new_state["done"] = all_sq_done(sqs, done_ids)


def _append_dynamic_subquestion_dag(
    new_state: AgentState,
    ctx: CriticApplyCtx,
    sqs: list[dict[str, Any]],
    done_ids: set[str],
) -> None:
    """BL-12: DAG-aware dynamic sub-question insertion — uses done_ids."""
    memory = ctx.memory
    new_sq = ctx.result.new_sub_question or ctx.sq_text
    if ctx.result.action == CriticAction.REWRITE:
        memory.exclude_hypothesis(ctx.sq_text)
    if memory.is_duplicate_subquestion(new_sq) or memory.is_excluded(new_sq):
        new_state["done_ids"] = list(done_ids)
        new_state["done"] = True
        return
    new_id = f"sq_dyn_{len(sqs) + 1}"
    insert_at = min(ctx.idx + 1, len(sqs))
    sqs = list(sqs)
    sqs.insert(
        insert_at,
        SubQuestion(
            id=new_id,
            text=new_sq,
            depends_on=[ctx.sq_id] if ctx.sq else [],
            rationale=ctx.result.rationale,
        ).model_dump(),
    )
    new_state["sub_questions"] = sqs
    new_state["done_ids"] = list(done_ids)