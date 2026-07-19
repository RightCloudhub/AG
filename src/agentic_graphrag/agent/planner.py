"""Planner: question → sub-question DAG (FR-AG-02 / P2-AG-01).

LLM and offline planning logic. DAG model / topo / materialize live in
:mod:`agentic_graphrag.agent.plan_dag`.
"""

from __future__ import annotations

import re

from agentic_graphrag.agent.entities import extract_entity_mentions
from agentic_graphrag.agent.plan_dag import (
    PlanResult,
    SubQuestion,
    materialize_subquestion,
    normalize_plan,
    ready_subquestions,
    topological_sort,
)
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
    ql = q.lower()
    entities = extract_entity_mentions(q, known_entities)
    primary = entities[0] if entities else None

    # Pattern: CEO of parent/parent company of X  → tree with placeholder
    if primary and re.search(r"ceo of (the )?parent", ql):
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=f"What is the parent company of {primary}?",
                    rationale="multi-hop: resolve parent first",
                ),
                SubQuestion(
                    id="sq2",
                    text="Who is the CEO of {from:sq1}?",
                    depends_on=["sq1"],
                    rationale="multi-hop: CEO of resolved parent",
                    is_placeholder=True,
                ),
            ]
        )

    # Pattern: companies CEO of X previously worked at
    if (
        primary
        and "ceo" in ql
        and any(k in ql for k in ("previously work", "worked at", "work at", "worked for"))
    ):
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=f"Who is the CEO of {primary}?",
                    rationale="resolve CEO",
                ),
                SubQuestion(
                    id="sq2",
                    text="Which companies did {from:sq1} previously work at?",
                    depends_on=["sq1"],
                    rationale="prior employers of resolved CEO",
                    is_placeholder=True,
                ),
            ]
        )

    # Pattern: CEO of company that competes with X
    if primary and "compet" in ql and "ceo" in ql:
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=f"Which company competes with {primary}?",
                    rationale="find competitor",
                ),
                SubQuestion(
                    id="sq2",
                    text="Who is the CEO of {from:sq1}?",
                    depends_on=["sq1"],
                    rationale="CEO of resolved competitor",
                    is_placeholder=True,
                ),
            ]
        )

    # Pattern: parent of producer of product
    if primary and "parent" in ql and any(k in ql for k in ("producer", "produce", "product")):
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=f"Which company produces {primary}?",
                    rationale="find producer",
                ),
                SubQuestion(
                    id="sq2",
                    text="What is the parent company of {from:sq1}?",
                    depends_on=["sq1"],
                    rationale="parent of resolved producer",
                    is_placeholder=True,
                ),
            ]
        )

    # Pattern: suppliers of product that also supply competitor
    if (
        len(entities) >= 1
        and "supplier" in ql
        and any(k in ql for k in ("also", "shared", "among"))
    ):
        ent = entities[0]
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=f"Which companies supply or supply for {ent}?",
                    rationale="list suppliers",
                ),
                SubQuestion(
                    id="sq2",
                    text=q,
                    depends_on=["sq1"],
                    rationale="intersect suppliers",
                ),
            ]
        )

    # Pattern: shared connections between A and B (parallel fan-out → tree join)
    # Checked before generic path/connection so "shared connections" stays a DAG.
    if len(entities) >= 2 and "shared" in ql:
        a, b = entities[0], entities[1]
        return normalize_plan(
            [
                SubQuestion(id="sq1", text=f"What are neighbors of {a}?", rationale="expand A"),
                SubQuestion(id="sq2", text=f"What are neighbors of {b}?", rationale="expand B"),
                SubQuestion(
                    id="sq3",
                    text=f"What shared connections exist between {a} and {b}?",
                    depends_on=["sq1", "sq2"],
                    rationale="join neighbor sets",
                ),
            ]
        )

    # Pattern: relationship chain / path between A and B
    if len(entities) >= 2 and any(
        k in ql for k in ("relationship", "chain", "path", "connects", "between")
    ):
        a, b = entities[0], entities[1]
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=f"What graph path connects {a} and {b}?",
                    rationale="path query",
                ),
            ]
        )

    # Pattern: both participated in event / did A and B both...
    if "both" in ql and len(entities) >= 2:
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=f"What relations involve {entities[0]}?",
                    rationale="facts about first entity",
                ),
                SubQuestion(
                    id="sq2",
                    text=f"What relations involve {entities[1]}?",
                    rationale="facts about second entity",
                ),
                SubQuestion(
                    id="sq3",
                    text=q,
                    depends_on=["sq1", "sq2"],
                    rationale="combine both entity facts",
                ),
            ]
        )

    # Default: single hop focused on primary entity
    if primary:
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=q,
                    rationale=f"single-hop around {primary}",
                )
            ]
        )
    return normalize_plan([SubQuestion(id="sq1", text=q, rationale="passthrough")])


def _split(text: str) -> tuple[str, str]:
    if "# System" in text and "# User" in text:
        parts = text.split("# User", 1)
        return parts[0].replace("# System", "", 1).strip(), parts[1].strip()
    return "You are a planner.", text
