"""Planner: question → sub-question chain (FR-AG-02, chain only in POC)."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from agentic_graphrag.agent.entities import extract_entity_mentions, primary_entity
from agentic_graphrag.config import load_prompt
from agentic_graphrag.llm.provider import LLMProvider, Message, Tier
from agentic_graphrag.llm.structured import complete_structured


class SubQuestion(BaseModel):
    id: str
    text: str
    depends_on: list[str] = Field(default_factory=list)
    rationale: str = ""


class PlanResult(BaseModel):
    sub_questions: list[SubQuestion] = Field(default_factory=list)


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
    return result.sub_questions


def plan_offline(
    question: str,
    *,
    known_entities: list[str] | None = None,
) -> list[SubQuestion]:
    """Deterministic multi-hop decomposition without LLM."""
    q = question.strip()
    ql = q.lower()
    entities = extract_entity_mentions(q, known_entities)
    primary = entities[0] if entities else None

    # Pattern: CEO of parent/parent company of X
    if primary and re.search(r"ceo of (the )?parent", ql):
        return [
            SubQuestion(
                id="sq1",
                text=f"What is the parent company of {primary}?",
                rationale="multi-hop: resolve parent first",
            ),
            SubQuestion(
                id="sq2",
                text=f"Who is the CEO of the parent company of {primary}?",
                depends_on=["sq1"],
                rationale="multi-hop: CEO of parent",
            ),
        ]

    # Pattern: companies CEO of X previously worked at
    if primary and "ceo" in ql and any(k in ql for k in ("previously work", "worked at", "work at", "worked for")):
        return [
            SubQuestion(
                id="sq1",
                text=f"Who is the CEO of {primary}?",
                rationale="resolve CEO",
            ),
            SubQuestion(
                id="sq2",
                text=f"Which companies did the CEO of {primary} previously work at?",
                depends_on=["sq1"],
                rationale="prior employers of CEO",
            ),
        ]

    # Pattern: CEO of company that competes with X
    if primary and "compet" in ql and "ceo" in ql:
        return [
            SubQuestion(
                id="sq1",
                text=f"Which company competes with {primary}?",
                rationale="find competitor",
            ),
            SubQuestion(
                id="sq2",
                text=f"Who is the CEO of the competitor of {primary}?",
                depends_on=["sq1"],
                rationale="CEO of competitor",
            ),
        ]

    # Pattern: parent of producer of product
    if primary and "parent" in ql and any(k in ql for k in ("producer", "produce", "product")):
        return [
            SubQuestion(
                id="sq1",
                text=f"Which company produces {primary}?",
                rationale="find producer",
            ),
            SubQuestion(
                id="sq2",
                text=f"What is the parent company of the producer of {primary}?",
                depends_on=["sq1"],
                rationale="parent of producer",
            ),
        ]

    # Pattern: suppliers of product that also supply competitor
    if len(entities) >= 1 and "supplier" in ql and any(k in ql for k in ("also", "shared", "among")):
        ent = entities[0]
        return [
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

    # Pattern: relationship chain between A and B
    if len(entities) >= 2 and any(k in ql for k in ("relationship", "chain", "connection", "path", "between")):
        a, b = entities[0], entities[1]
        return [
            SubQuestion(
                id="sq1",
                text=f"What graph path connects {a} and {b}?",
                rationale="path query",
            ),
        ]

    # Pattern: shared connections between A and B
    if len(entities) >= 2 and "shared" in ql:
        a, b = entities[0], entities[1]
        return [
            SubQuestion(id="sq1", text=f"What are neighbors of {a}?", rationale="expand A"),
            SubQuestion(
                id="sq2",
                text=f"What are neighbors of {b}?",
                depends_on=["sq1"],
                rationale="expand B",
            ),
        ]

    # Pattern: both participated in event / did A and B both...
    if "both" in ql and len(entities) >= 2:
        return [
            SubQuestion(
                id="sq1",
                text=f"What relations involve {entities[0]}?",
                rationale="facts about first entity",
            ),
            SubQuestion(
                id="sq2",
                text=f"What relations involve {entities[1]}?",
                depends_on=["sq1"],
                rationale="facts about second entity",
            ),
        ]

    # Default: single hop focused on primary entity
    if primary:
        return [
            SubQuestion(
                id="sq1",
                text=q,
                rationale=f"single-hop around {primary}",
            )
        ]
    return [SubQuestion(id="sq1", text=q, rationale="passthrough")]


def _split(text: str) -> tuple[str, str]:
    if "# System" in text and "# User" in text:
        parts = text.split("# User", 1)
        return parts[0].replace("# System", "", 1).strip(), parts[1].strip()
    return "You are a planner.", text
