"""Helper logic for AgentRuntime node handlers (keeps loop_runtime lean)."""

from __future__ import annotations

from dataclasses import dataclass

from agentic_graphrag.agent.critic import CriticAction, CriticResult
from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig, Guardrails
from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.parallel_branches import (
    PARALLEL_GROUPS_KEY,
    PREFETCH_KEY,
    retrieve_for_node,
)
from agentic_graphrag.agent.plan_coverage import record_dropped_subquestion
from agentic_graphrag.agent.planner import SubQuestion, materialize_subquestion
from agentic_graphrag.agent.state import AgentState
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.reasoning import ReasoningStep


@dataclass
class ExecutorNodeCtx:
    """Mutable pieces shared while executing one sub-question."""

    state: AgentState
    sq: SubQuestion
    sqs: list[SubQuestion]
    idx: int
    memory: MemoryState
    guards: Guardrails
    executor: Executor
    llm: LLMClient | None


@dataclass
class CriticApplyCtx:
    """Inputs for applying a critic decision onto agent state."""

    state: AgentState
    result: CriticResult
    sq: SubQuestion | None
    sq_text: str
    sq_id: str
    idx: int
    remaining: int
    memory: MemoryState
    guards: Guardrails
    guard_cfg: GuardrailConfig


def skip_excluded_or_duplicate(ctx: ExecutorNodeCtx) -> AgentState | None:
    """Return early state when sub-question is excluded/duplicate; else None."""
    sq, sqs, idx = ctx.sq, ctx.sqs, ctx.idx
    memory, guards, state = ctx.memory, ctx.guards, ctx.state
    hop = guards.state.hop
    hop_cap = hop >= guards.config.max_hops
    # A slot whose dependency produced no conclusion stays "{from:sqN}"; running
    # retrieval on that literal text only fetches noise (and spends a hop).
    if memory.is_excluded(sq.text) or sq.unresolved_placeholders():
        memory.mark_subquestion_done(sq.id)
        return {
            **state,
            "sub_questions": sqs,
            "current_index": idx + 1,
            "hop": hop,
            "done": hop_cap or idx + 1 >= len(sqs),
            "guardrail_status": guards.status_text(),
            "memory": memory.copy(),
        }
    if memory.is_duplicate_subquestion(sq.text) and idx > 0:
        memory.exclude_hypothesis(sq.text)
        return {
            **state,
            "sub_questions": sqs,
            "current_index": idx + 1,
            "hop": hop,
            "guardrail_status": guards.status_text(),
            "done": hop_cap or idx + 1 >= len(sqs),
            "memory": memory.copy(),
        }
    return None


def execute_subquestion(ctx: ExecutorNodeCtx) -> AgentState:
    memory, guards, sq = ctx.memory, ctx.guards, ctx.sq
    memory.mark_subquestion(sq.text)
    (candidates, traces), prefetch, group = retrieve_for_node(ctx)
    memory.add_evidence(candidates)
    # Record every id this step retrieved, not only ids new to the pool: the
    # critic reads the step's evidence_ids as "this hop's evidence", and a hit
    # already pooled by an earlier/sibling hop (same edge id) used to vanish,
    # leaving only off-target edges (e.g. another company's CEO_OF).
    step_ids = list(dict.fromkeys(c.id for c in candidates))
    step = ReasoningStep(
        hop=guards.state.hop,
        sub_question=sq.text,
        depends_on=sq.depends_on,
        tool_calls=traces,
        evidence_ids=step_ids,
    )
    chain = ctx.state["chain"].model_copy(deep=True)
    chain.steps.append(step)
    chain.explored_paths = sorted(memory.explored_paths)
    if len(group) > 1:
        groups = list((chain.metadata or {}).get(PARALLEL_GROUPS_KEY) or [])
        chain.metadata = {**(chain.metadata or {}), PARALLEL_GROUPS_KEY: [*groups, group]}
    return {
        **ctx.state,
        PREFETCH_KEY: prefetch,
        "sub_questions": ctx.sqs,
        "hop": guards.state.hop,
        "evidence": dict(memory.evidence),
        "chain": chain,
        "memory_summary": memory.summary(),
        "memory": memory.copy(),
        "guardrail_status": guards.status_text(),
    }


def materialize_current(
    sqs: list[SubQuestion],
    idx: int,
    memory: MemoryState,
) -> tuple[SubQuestion, list[SubQuestion]]:
    sq = sqs[idx]
    sq = materialize_subquestion(sq, memory.conclusions_by_subquestion)
    sqs = list(sqs)
    sqs[idx] = sq
    return sq, sqs


def apply_critic_result(ctx: CriticApplyCtx) -> AgentState:
    _record_critic_on_chain(ctx)
    new_state = _base_state_after_critic(ctx)
    sqs = list(ctx.state.get("sub_questions") or [])

    # Guardrail trip / give_up always terminate (even with remaining DAG nodes).
    if ctx.guards.state.tripped or ctx.result.action == CriticAction.GIVE_UP:
        if ctx.result.action == CriticAction.GIVE_UP:
            ctx.memory.exclude_hypothesis(ctx.sq_text)
        new_state["done"] = True
        return _cap_hops(new_state, ctx.guards, ctx.guard_cfg)

    # Planned remaining nodes: only advance on SUFFICIENT (sub-question done).
    # NEXT_HOP / REWRITE still go through terminal resolution so rewrites apply.
    if ctx.remaining > 0 and ctx.result.action == CriticAction.SUFFICIENT:
        new_state["current_index"] = ctx.idx + 1
        new_state["done"] = ctx.guards.state.hop >= ctx.guard_cfg.max_hops
        return _cap_hops(new_state, ctx.guards, ctx.guard_cfg)

    _resolve_terminal_action(new_state, ctx, sqs)
    return _cap_hops(new_state, ctx.guards, ctx.guard_cfg)


def _record_critic_on_chain(ctx: CriticApplyCtx) -> str:
    result, memory = ctx.result, ctx.memory
    chain = ctx.state["chain"].model_copy(deep=True)
    conclusion = result.partial_answer or ""
    if chain.steps:
        chain.steps[-1].critic_action = result.action
        if conclusion:
            chain.steps[-1].conclusion = conclusion
    if result.sub_answered or result.action == CriticAction.SUFFICIENT or conclusion:
        memory.mark_subquestion_done(ctx.sq_id, conclusion or None)
    # Mutate ctx.state chain via side channel used by _base_state_after_critic
    ctx.state = {**ctx.state, "chain": chain}
    return conclusion


def _base_state_after_critic(ctx: CriticApplyCtx) -> AgentState:
    memory, guards = ctx.memory, ctx.guards
    return {
        **ctx.state,
        "guardrail_status": guards.status_text(),
        "memory": memory.copy(),
        "memory_summary": memory.summary(),
    }


def _cap_hops(
    new_state: AgentState,
    guards: Guardrails,
    guard_cfg: GuardrailConfig,
) -> AgentState:
    if guards.state.hop >= guard_cfg.max_hops:
        new_state["done"] = True
        new_state["guardrail_status"] = guards.status_text()
    return new_state


def _resolve_terminal_action(
    new_state: AgentState,
    ctx: CriticApplyCtx,
    sqs: list[SubQuestion],
) -> None:
    result, memory, guards = ctx.result, ctx.memory, ctx.guards
    action = result.action
    if action == CriticAction.SUFFICIENT:
        new_state["done"] = True
        return
    if action == CriticAction.GIVE_UP or guards.state.tripped:
        if action == CriticAction.GIVE_UP:
            memory.exclude_hypothesis(ctx.sq_text)
        new_state["done"] = True
        return
    if action in (CriticAction.NEXT_HOP, CriticAction.REWRITE):
        _append_dynamic_subquestion(new_state, ctx, sqs)
        return
    new_state["current_index"] = ctx.idx + 1
    if new_state["current_index"] >= len(sqs):
        new_state["done"] = True


def _append_dynamic_subquestion(
    new_state: AgentState,
    ctx: CriticApplyCtx,
    sqs: list[SubQuestion],
) -> None:
    """Insert critic follow-up after the current node so remaining plan nodes run.

    Previously the dynamic SQ was appended at the end and ``current_index`` jumped
    there, permanently skipping any planned nodes that had not yet executed.
    """
    memory = ctx.memory
    new_sq = ctx.result.new_sub_question or ctx.sq_text
    if ctx.result.action == CriticAction.REWRITE:
        memory.exclude_hypothesis(ctx.sq_text)
    if memory.is_duplicate_subquestion(new_sq) or memory.is_excluded(new_sq):
        new_state["done"] = True
        return
    if len(sqs) >= ctx.guard_cfg.max_sub_questions:
        # Breadth budget is exhausted; stop instead of growing the plan into
        # nodes the hop budget can never reach (BL-12).
        record_dropped_subquestion(new_state, new_sq)
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
        ),
    )
    new_state["sub_questions"] = sqs
    # Next executor hop runs the inserted node; original tail still follows.
    new_state["current_index"] = insert_at


def load_evidence(state: AgentState) -> list[Candidate]:
    return list(state.get("evidence", {}).values())


def load_evidence_for_critic(state: AgentState) -> list[Candidate]:
    """Prefer current-hop evidence for partial_answer; fall back to full pool.

    Using the entire memory pool made hop-1 PARENT_OF edges outrank hop-2 CEO
    edges when ``extract_entity_conclusion`` scanned in insertion order.
    """
    all_ev = load_evidence(state)
    if not all_ev:
        return all_ev
    chain = state.get("chain")
    if chain is None or not chain.steps:
        return all_ev
    ids = set(chain.steps[-1].evidence_ids)
    if not ids:
        return all_ev
    local = [c for c in all_ev if c.id in ids]
    return local if local else all_ev
