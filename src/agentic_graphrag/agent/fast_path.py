"""Fast Path: single-round vector/fulltext RAG + answer (FR-AG-01)."""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.core.constants import MILLISECONDS_PER_SECOND
from agentic_graphrag.core.enums import QueryRoute, StepAction
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.ports.llm import LLMClient, Tier
from agentic_graphrag.core.reasoning import ReasoningChain, ReasoningStep, ToolCallTrace
from agentic_graphrag.generation.answer import generate_answer
from agentic_graphrag.generation.confidence import grade_confidence
from agentic_graphrag.governance.budget import BudgetTracker


def run_fast_path(
    question: str,
    executor: Executor,
    llm: LLMClient | None,
    *,
    allow_llm: bool = True,
    budget: BudgetTracker | None = None,
    triage_meta: dict[str, Any] | None = None,
    tenant_id: str | None = None,
) -> ReasoningChain:
    """Single-hop retrieval + generation without Planner/Critic loop."""
    chain, _ = run_fast_path_with_evidence(
        question,
        executor,
        llm,
        allow_llm=allow_llm,
        budget=budget,
        triage_meta=triage_meta,
        tenant_id=tenant_id,
    )
    return chain


def run_fast_path_with_evidence(
    question: str,
    executor: Executor,
    llm: LLMClient | None,
    *,
    allow_llm: bool = True,
    budget: BudgetTracker | None = None,
    triage_meta: dict[str, Any] | None = None,
    tenant_id: str | None = None,
) -> tuple[ReasoningChain, list[Candidate]]:
    """Fast path that also returns its evidence (inherited on escalation)."""
    t0 = time.perf_counter()
    chain = _new_chain(question, triage_meta)
    candidates, traces = executor.run(question, allow_llm=False, tenant_id=tenant_id)
    chain.steps.append(_fast_step(question, candidates, traces))
    chain = generate_answer(
        chain,
        candidates,
        llm,
        conclusions="",
        guardrail_status="fast_path",
        allow_llm=allow_llm and llm is not None,
        tier=Tier.LIGHT,
    )
    conf = grade_confidence(chain, candidates)
    chain.metadata = {**(chain.metadata or {}), "confidence": conf}
    chain.cost.latency_ms = int((time.perf_counter() - t0) * MILLISECONDS_PER_SECOND)
    _apply_budget(chain, budget)
    return chain, candidates


def _new_chain(question: str, triage_meta: dict[str, Any] | None) -> ReasoningChain:
    chain = ReasoningChain(question=question, route=QueryRoute.FAST_PATH, query_id=str(uuid4()))
    if triage_meta:
        chain.metadata = {**(chain.metadata or {}), "triage": triage_meta}
    return chain


def _fast_step(
    question: str, candidates: list[Candidate], traces: list[ToolCallTrace]
) -> ReasoningStep:
    return ReasoningStep(
        hop=1,
        sub_question=question,
        tool_calls=traces,
        evidence_ids=[c.id for c in candidates[:20]],
        conclusion="",
        critic_action=StepAction.FAST_PATH,
    )


def _apply_budget(chain: ReasoningChain, budget: BudgetTracker | None) -> None:
    if not budget:
        return
    snap = budget.snapshot()
    chain.cost.llm_calls = snap["llm_calls"]
    chain.cost.tokens = snap["total_tokens"]
    chain.cost.prompt_tokens = snap["prompt_tokens"]
    chain.cost.completion_tokens = snap["completion_tokens"]


def evidence_from_chain_candidates(candidates: list[Candidate]) -> list[Candidate]:
    return list(candidates)
