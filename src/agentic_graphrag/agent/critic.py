"""Critic: evidence sufficiency and next-hop decisions (FR-AG-04)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from agentic_graphrag.config import load_prompt
from agentic_graphrag.llm.provider import LLMProvider, Message, Tier
from agentic_graphrag.llm.structured import complete_structured
from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource


class CriticAction(StrEnum):
    SUFFICIENT = "sufficient"
    NEXT_HOP = "next_hop"
    REWRITE = "rewrite"
    GIVE_UP = "give_up"


class CriticResult(BaseModel):
    action: CriticAction
    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    new_sub_question: str | None = None
    partial_answer: str | None = None


def critique(
    question: str,
    sub_question: str,
    evidence: list[Candidate],
    explored_paths: list[str],
    llm: LLMProvider | None,
    *,
    allow_llm: bool = True,
    hop: int = 1,
    max_hops: int = 5,
    remaining_subquestions: int = 0,
) -> CriticResult:
    if not evidence:
        if hop >= max_hops:
            return CriticResult(action=CriticAction.GIVE_UP, rationale="no evidence and hop limit")
        return CriticResult(
            action=CriticAction.NEXT_HOP,
            rationale="no evidence yet",
            new_sub_question=sub_question,
        )

    if not allow_llm or llm is None:
        return _offline_critique(
            question,
            sub_question,
            evidence,
            hop=hop,
            max_hops=max_hops,
            remaining_subquestions=remaining_subquestions,
        )

    prompt = load_prompt("critic")
    evidence_list = "\n".join(f"[{c.id}] {c.content[:300]}" for c in evidence[:20])
    system, user = _split(
        prompt.format(
            question=question,
            sub_question=sub_question,
            evidence_list=evidence_list or "(none)",
            explored_paths="; ".join(explored_paths[:20]) or "(none)",
        )
    )
    return complete_structured(
        llm,
        [Message(role="system", content=system), Message(role="user", content=user)],
        CriticResult,
        tier=Tier.STRONG,
    )


def _offline_critique(
    question: str,
    sub_question: str,
    evidence: list[Candidate],
    *,
    hop: int,
    max_hops: int,
    remaining_subquestions: int,
) -> CriticResult:
    graph_hits = [c for c in evidence if c.source == CandidateSource.GRAPH]
    eids = [c.id for c in evidence[:8]]
    partial = graph_hits[0].content if graph_hits else (evidence[0].content if evidence else None)

    # If planner left more planned sub-questions, continue the chain
    if remaining_subquestions > 0 and hop < max_hops:
        return CriticResult(
            action=CriticAction.SUFFICIENT,  # advance index handled by loop when not last
            rationale="offline: continue planned chain via index advance",
            evidence_ids=eids,
            partial_answer=partial,
        )

    # Graph evidence present → treat as enough for offline POC generation
    if graph_hits:
        return CriticResult(
            action=CriticAction.SUFFICIENT,
            rationale="offline: graph evidence available",
            evidence_ids=eids,
            partial_answer=partial,
        )

    if len(evidence) >= 3 or hop >= max_hops:
        return CriticResult(
            action=CriticAction.SUFFICIENT if evidence else CriticAction.GIVE_UP,
            rationale="offline: text evidence only or hop limit",
            evidence_ids=eids,
            partial_answer=partial,
        )

    return CriticResult(
        action=CriticAction.NEXT_HOP,
        rationale="offline: need more evidence",
        new_sub_question=sub_question,
        evidence_ids=eids,
        partial_answer=partial,
    )


def _split(text: str) -> tuple[str, str]:
    if "# System" in text and "# User" in text:
        parts = text.split("# User", 1)
        return parts[0].replace("# System", "", 1).strip(), parts[1].strip()
    return "You are a critic.", text
