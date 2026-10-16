"""Planner: question → sub-question DAG (FR-AG-02 / P2-AG-01).

LLM and offline planning logic. DAG model / topo / materialize live in
:mod:`agentic_graphrag.agent.plan_dag`. Offline patterns live in
:mod:`agentic_graphrag.agent.planner_patterns`.
"""

from __future__ import annotations

from agentic_graphrag.agent.entities import extract_entity_mentions
from agentic_graphrag.agent.plan_dag import (
    PlanResult,
    SubQuestion,
    materialize_subquestion,
    normalize_plan,
    ready_subquestions,
    topological_sort,
)
from agentic_graphrag.agent.planner_patterns import OFFLINE_MATCHERS, PlanContext
from agentic_graphrag.config import load_prompt
from agentic_graphrag.llm.provider import LLMProvider, Message, Tier
from agentic_graphrag.llm.structured import complete_structured

# Backward-compatible re-exports.
__all__ = [
    "PlanResult",
    "SubQuestion",
    "materialize_subquestion",
    "normalize_plan",
    "plan",
    "plan_offline",
    "ready_subquestions",
    "topological_sort",
]


def plan(
    question: str,
    memory_summary: str,
    llm: LLMProvider | None,
    *,
    allow_llm: bool = True,
    known_entities: list[str] | None = None,
) -> list[SubQuestion]:
    if not allow_llm or llm is None:
        return plan_offline(question, known_entities=known_entities)

    prompt = load_prompt("planner")
    system, user = _split(
        prompt.format(question=question, memory_summary=memory_summary or "(empty)")
    )
    result = complete_structured(
        llm,
        [Message(role="system", content=system), Message(role="user", content=user)],
        PlanResult,
        tier=Tier.STRONG,
    )
    if not result.sub_questions:
        return plan_offline(question, known_entities=known_entities)
    return normalize_plan(result.sub_questions)


def plan_offline(
    question: str,
    *,
    known_entities: list[str] | None = None,
) -> list[SubQuestion]:
    """Deterministic multi-hop DAG decomposition without LLM."""
    q = question.strip()
    entities = extract_entity_mentions(q, known_entities)
    ctx = PlanContext(
        q=q,
        ql=q.lower(),
        entities=entities,
        primary=entities[0] if entities else None,
    )
    for matcher in OFFLINE_MATCHERS:
        hit = matcher(ctx)
        if hit is not None:
            return normalize_plan(hit)
    return normalize_plan([SubQuestion(id="sq1", text=q, rationale="passthrough")])


def _split(text: str) -> tuple[str, str]:
    if "# System" in text and "# User" in text:
        parts = text.split("# User", 1)
        return parts[0].replace("# System", "", 1).strip(), parts[1].strip()
    return "You are a planner.", text
