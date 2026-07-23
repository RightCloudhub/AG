"""Helper logic for AgentRuntime node handlers (keeps loop_runtime lean)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentic_graphrag.agent.critic import CriticAction, CriticResult
from agentic_graphrag.agent.entities import extract_entity_mentions
from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig, Guardrails
from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.planner import SubQuestion, materialize_subquestion
from agentic_graphrag.generation.trace import ReasoningChain, ReasoningStep
from agentic_graphrag.llm.provider import LLMProvider
from agentic_graphrag.retrieval.contracts import Candidate

AgentState = dict[str, Any]


@dataclass
class ExecutorNodeCtx:
    """Mutable pieces shared while executing one sub-question."""

    state: AgentState
    sq: SubQuestion
    sqs: list[dict[str, Any]]
    idx: int
    memory: MemoryState
    guards: Guardrails
    executor: Executor
    llm: LLMProvider | None


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


def collect_entity_hints(ctx: ExecutorNodeCtx) -> list[str]:
    known = ctx.executor.known_entities or None
    hints = extract_entity_mentions(ctx.state["question"] + " " + ctx.sq.text, known)
    for conc in ctx.memory.conclusions_by_subquestion.values():
        hints.extend(extract_entity_mentions(conc, known))
    return hints


def skip_excluded_or_duplicate(ctx: ExecutorNodeCtx) -> AgentState | None:
    """Return early state when sub-question is excluded/duplicate; else None."""
    sq, sqs, idx = ctx.sq, ctx.sqs, ctx.idx
    memory, guards, state = ctx.memory, ctx.guards, ctx.state
    if memory.is_excluded(sq.text):
        memory.mark_subquestion_done(sq.id)
        return {
            **state,
            "sub_questions": sqs,
            "current_index": idx + 1,
            "done": idx + 1 >= len(sqs),
            "guardrail_status": guards.status_text(),
            "memory_snapshot": memory.to_snapshot(),
        }
    if memory.is_duplicate_subquestion(sq.text) and idx > 0:
        memory.exclude_hypothesis(sq.text)
        return {
            **state,
            "sub_questions": sqs,
            "current_index": idx + 1,
            "guardrail_status": guards.status_text(),
            "done": idx + 1 >= len(sqs),
            "memory_snapshot": memory.to_snapshot(),
        }
    return None


def execute_subquestion(ctx: ExecutorNodeCtx) -> AgentState:
    memory, guards, sq = ctx.memory, ctx.guards, ctx.sq
    memory.mark_subquestion(sq.text)
    allow_llm = bool(ctx.state.get("allow_llm", True))
    candidates, traces = ctx.executor.run(
        sq.text,
        entities_hint=collect_entity_hints(ctx),
        allow_llm=allow_llm and ctx.llm is not None,
    )
    added = memory.add_evidence(candidates)
    step = ReasoningStep(
        hop=guards.state.hop,
        sub_question=sq.text,
        depends_on=sq.depends_on,
        tool_calls=traces,
        evidence_ids=added,
    )
    chain = ReasoningChain.model_validate(ctx.state["chain"])
    chain.steps.append(step)
    chain.explored_paths = sorted(memory.explored_paths)
    return {
        **ctx.state,
        "sub_questions": ctx.sqs,
        "hop": guards.state.hop,
        "evidence": [c.model_dump() for c in memory.evidence_list()],
        "chain": chain.model_dump(),
        "memory_summary": memory.summary(),
        "memory_snapshot": memory.to_snapshot(),
        "guardrail_status": guards.status_text(),
    }


def materialize_current(
    sqs: list[dict[str, Any]],
    idx: int,
    memory: MemoryState,
) -> tuple[SubQuestion, list[dict[str, Any]]]:
    sq = SubQuestion.model_validate(sqs[idx])
    sq = materialize_subquestion(sq, memory.conclusions_by_subquestion)
    sqs = list(sqs)
    sqs[idx] = sq.model_dump()
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
    chain = ReasoningChain.model_validate(ctx.state["chain"])
    conclusion = result.partial_answer or ""
    if chain.steps:
        chain.steps[-1].critic_action = result.action.value
        if conclusion:
            chain.steps[-1].conclusion = conclusion
    if result.sub_answered or result.action == CriticAction.SUFFICIENT or conclusion:
        memory.mark_subquestion_done(ctx.sq_id, conclusion or None)
    # Mutate ctx.state chain via side channel used by _base_state_after_critic
    ctx.state = {**ctx.state, "chain": chain.model_dump()}
    return conclusion


def _base_state_after_critic(ctx: CriticApplyCtx) -> AgentState:
    memory, guards = ctx.memory, ctx.guards
    return {
        **ctx.state,
        "guardrail_status": guards.status_text(),
        "memory_snapshot": memory.to_snapshot(),
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
    sqs: list[dict[str, Any]],
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
    sqs: list[dict[str, Any]],
) -> None:
    memory = ctx.memory
    new_sq = ctx.result.new_sub_question or ctx.sq_text
    if ctx.result.action == CriticAction.REWRITE:
        memory.exclude_hypothesis(ctx.sq_text)
    if memory.is_duplicate_subquestion(new_sq) or memory.is_excluded(new_sq):
        new_state["done"] = True
        return
    new_id = f"sq_dyn_{len(sqs) + 1}"
    sqs = list(sqs) + [
        SubQuestion(
            id=new_id,
            text=new_sq,
            depends_on=[ctx.sq_id] if ctx.sq else [],
            rationale=ctx.result.rationale,
        ).model_dump()
    ]
    new_state["sub_questions"] = sqs
    new_state["current_index"] = len(sqs) - 1


def load_evidence(state: AgentState) -> list[Candidate]:
    return [Candidate.model_validate(e) for e in state.get("evidence") or []]


def load_evidence_for_critic(state: AgentState) -> list[Candidate]:
    """Prefer current-hop evidence for partial_answer; fall back to full pool.

    Using the entire memory pool made hop-1 PARENT_OF edges outrank hop-2 CEO
    edges when ``extract_entity_conclusion`` scanned in insertion order.
    """
    all_ev = load_evidence(state)
    if not all_ev:
        return all_ev
    chain = state.get("chain") or {}
    steps = chain.get("steps") if isinstance(chain, dict) else None
    if not steps:
        return all_ev
    last = steps[-1] if isinstance(steps[-1], dict) else None
    if not last:
        return all_ev
    ids = set(last.get("evidence_ids") or [])
    if not ids:
        return all_ev
    local = [c for c in all_ev if c.id in ids]
    return local if local else all_ev
