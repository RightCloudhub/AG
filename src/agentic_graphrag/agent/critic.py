"""Critic: evidence sufficiency and next-hop decisions (FR-AG-04 / P2-AG-02).

Two-level judgment:
- **sub_question**: can current evidence answer the active sub-question?
- **global**: can all evidence answer the original question?

Actions: sufficient | next_hop | rewrite | give_up
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from agentic_graphrag.agent.critic_offline import (
    extract_entity_conclusion,
    offline_critique,
)
from agentic_graphrag.agent.options import CritiqueContext
from agentic_graphrag.config import load_prompt
from agentic_graphrag.llm.provider import LLMProvider, Message, Tier
from agentic_graphrag.llm.structured import complete_structured

_EVIDENCE_PROMPT_CAP = 20
_EVIDENCE_CONTENT_CHARS = 300

__all__ = [
    "CriticAction",
    "CriticScope",
    "CriticResult",
    "CritiqueContext",
    "critique",
    "extract_entity_conclusion",
]


class CriticAction(StrEnum):
    SUFFICIENT = "sufficient"
    NEXT_HOP = "next_hop"
    REWRITE = "rewrite"
    GIVE_UP = "give_up"


class CriticScope(StrEnum):
    SUB_QUESTION = "sub_question"
    GLOBAL = "global"


class CriticResult(BaseModel):
    action: CriticAction
    scope: CriticScope = CriticScope.SUB_QUESTION
    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    new_sub_question: str | None = None
    partial_answer: str | None = None
    sub_answered: bool = False
    global_answered: bool = False


def critique(
    question: str | CritiqueContext,
    llm: LLMProvider | None = None,
    *rest: Any,
    allow_llm: bool = True,
    sub_question: str = "",
    evidence: list[Any] | None = None,
    explored_paths: list[str] | None = None,
    hop: int = 1,
    max_hops: int = 5,
    remaining_subquestions: int = 0,
    excluded_hypotheses: list[str] | None = None,
) -> CriticResult:
    """Judge evidence sufficiency.

    Preferred::
        critique(CritiqueContext(...), llm, allow_llm=True)

    Legacy (still supported)::
        critique(question, sub_question, evidence, explored_paths, llm, *, hop=...)
        critique(question, llm=llm, sub_question=..., evidence=..., ...)
    """
    ctx, resolved_llm = _resolve_critique_args(
        question,
        llm,
        rest,
        sub_question=sub_question,
        evidence=evidence,
        explored_paths=explored_paths,
        hop=hop,
        max_hops=max_hops,
        remaining_subquestions=remaining_subquestions,
        excluded_hypotheses=excluded_hypotheses,
    )
    return _critique_impl(ctx, resolved_llm, allow_llm=allow_llm)


def _resolve_critique_args(
    question: str | CritiqueContext,
    llm: LLMProvider | None,
    rest: tuple[Any, ...],
    *,
    sub_question: str,
    evidence: list[Any] | None,
    explored_paths: list[str] | None,
    hop: int,
    max_hops: int,
    remaining_subquestions: int,
    excluded_hypotheses: list[str] | None,
) -> tuple[CritiqueContext, LLMProvider | None]:
    if isinstance(question, CritiqueContext):
        return question, llm
    if isinstance(llm, str) and rest:
        return _legacy_positional(
            question,
            llm,
            rest,
            hop=hop,
            max_hops=max_hops,
            remaining_subquestions=remaining_subquestions,
            excluded_hypotheses=excluded_hypotheses,
        )
    ctx = CritiqueContext(
        question=question,
        sub_question=sub_question,
        evidence=list(evidence or []),
        explored_paths=list(explored_paths or []),
        hop=hop,
        max_hops=max_hops,
        remaining_subquestions=remaining_subquestions,
        excluded_hypotheses=list(excluded_hypotheses or []),
    )
    return ctx, llm


def _legacy_positional(
    question: str,
    sub_question: str,
    rest: tuple[Any, ...],
    *,
    hop: int,
    max_hops: int,
    remaining_subquestions: int,
    excluded_hypotheses: list[str] | None,
) -> tuple[CritiqueContext, LLMProvider | None]:
    """Unpack critique(q, sq, evidence, paths, llm, ...)."""
    evidence = list(rest[0] or []) if len(rest) > 0 else []
    paths = list(rest[1] or []) if len(rest) > 1 else []
    resolved_llm = rest[2] if len(rest) > 2 else None
    ctx = CritiqueContext(
        question=question,
        sub_question=sub_question,
        evidence=evidence,
        explored_paths=paths,
        hop=hop,
        max_hops=max_hops,
        remaining_subquestions=remaining_subquestions,
        excluded_hypotheses=list(excluded_hypotheses or []),
    )
    return ctx, resolved_llm


def _critique_impl(
    ctx: CritiqueContext,
    llm: LLMProvider | None,
    *,
    allow_llm: bool,
) -> CriticResult:
    if not ctx.evidence:
        return _critique_no_evidence(ctx)
    if not allow_llm or llm is None:
        return offline_critique(ctx)
    return _llm_critique(ctx, llm)


def _critique_no_evidence(ctx: CritiqueContext) -> CriticResult:
    if ctx.hop >= ctx.max_hops:
        return CriticResult(
            action=CriticAction.GIVE_UP,
            scope=CriticScope.GLOBAL,
            rationale="no evidence and hop limit",
        )
    return CriticResult(
        action=CriticAction.NEXT_HOP,
        scope=CriticScope.SUB_QUESTION,
        rationale="no evidence yet",
        new_sub_question=ctx.sub_question,
    )


def _llm_critique(ctx: CritiqueContext, llm: LLMProvider) -> CriticResult:
    prompt = load_prompt("critic")
    evidence_list = "\n".join(
        f"[{c.id}] {c.content[:_EVIDENCE_CONTENT_CHARS]}"
        for c in ctx.evidence[:_EVIDENCE_PROMPT_CAP]
    )
    system, user = _split(
        prompt.format(
            question=ctx.question,
            sub_question=ctx.sub_question,
            evidence_list=evidence_list or "(none)",
            explored_paths="; ".join(ctx.explored_paths[:_EVIDENCE_PROMPT_CAP])
            or "(none)",
        )
    )
    user = (
        user
        + "\n\nJudge BOTH levels: set sub_answered if the sub-question is covered; "
        "set global_answered only if the original question can be fully answered. "
        "If sub is done but global is not, action=next_hop with a new sub-question "
        "(or rewrite the current one). Prefer rewrite when the sub-question wording "
        "is the bottleneck."
    )
    raw = complete_structured(
        llm,
        [Message(role="system", content=system), Message(role="user", content=user)],
        CriticResult,
        tier=Tier.STRONG,
    )
    return _normalize_scope(raw, remaining_subquestions=ctx.remaining_subquestions)


def _normalize_scope(result: CriticResult, *, remaining_subquestions: int) -> CriticResult:
    if result.action == CriticAction.SUFFICIENT:
        result.sub_answered = True
        if remaining_subquestions > 0:
            result.global_answered = False
            result.scope = CriticScope.SUB_QUESTION
        else:
            result.global_answered = True
            result.scope = CriticScope.GLOBAL
    elif result.action in (CriticAction.NEXT_HOP, CriticAction.REWRITE):
        result.scope = CriticScope.SUB_QUESTION
        result.global_answered = False
    elif result.action == CriticAction.GIVE_UP:
        result.scope = CriticScope.GLOBAL
    return result


def _split(text: str) -> tuple[str, str]:
    if "# System" in text and "# User" in text:
        parts = text.split("# User", 1)
        return parts[0].replace("# System", "", 1).strip(), parts[1].strip()
    return "You are a critic.", text
