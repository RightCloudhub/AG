"""Fast Path: single-round vector/fulltext RAG + answer (FR-AG-01)."""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.generation.answer import generate_answer
from agentic_graphrag.generation.confidence import grade_confidence
from agentic_graphrag.generation.trace import ReasoningChain, ReasoningStep
from agentic_graphrag.llm.budget import BudgetTracker
from agentic_graphrag.llm.provider import LLMProvider, Tier
from agentic_graphrag.retrieval.contracts import Candidate


def run_fast_path(
    question: str,
    executor: Executor,
    llm: LLMProvider | None,
    *,
    allow_llm: bool = True,
    budget: BudgetTracker | None = None,
    triage_meta: dict[str, Any] | None = None,
) -> ReasoningChain:
    """Single-hop retrieval + generation without Planner/Critic loop."""
    t0 = time.perf_counter()
    chain = _new_chain(question, triage_meta)
    candidates, traces = executor.run(question, allow_llm=False)
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
    chain.cost.latency_ms = int((time.perf_counter() - t0) * 1000)
    _apply_budget(chain, budget)
    return chain


def _new_chain(question: str, triage_meta: dict[str, Any] | None) -> ReasoningChain:
    chain = ReasoningChain(question=question, route="fast_path", query_id=str(uuid4()))
    if triage_meta:
        chain.metadata = {**(chain.metadata or {}), "triage": triage_meta}
    return chain


def _fast_step(question: str, candidates: list[Candidate], traces: list) -> ReasoningStep:
    return ReasoningStep(
        hop=1,
        sub_question=question,
        tool_calls=traces,
        evidence_ids=[c.id for c in candidates[:20]],
        conclusion="",
        critic_action="fast_path",
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
