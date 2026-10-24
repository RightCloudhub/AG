"""Critic: evidence sufficiency and next-hop decisions (FR-AG-04 / P2-AG-02).

Two-level judgment:
- **sub_question**: can current evidence answer the active sub-question?
- **global**: can all evidence answer the original question?

Actions: sufficient | next_hop | rewrite | give_up
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, TypeAdapter

from agentic_graphrag.agent.critic_offline import (
    extract_entity_conclusion,
    offline_critique,
)
from agentic_graphrag.agent.options import CritiqueContext
from agentic_graphrag.config import load_prompt
from agentic_graphrag.core.constants import PROMPT_SYSTEM_HEADER, PROMPT_USER_HEADER
from agentic_graphrag.core.enums import CriticAction, CriticMode, MessageRole, PromptName
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.ports.llm import CompletionClient, Message, Tier
from agentic_graphrag.llm.structured import complete_structured

_EVIDENCE_PROMPT_CAP = 20
_EVIDENCE_CONTENT_CHARS = 300
_EVIDENCE_LIST = TypeAdapter(list[Candidate])
_PATH_LIST = TypeAdapter(list[str])
_LEGACY_EVIDENCE_INDEX = 0
_LEGACY_PATHS_INDEX = 1
_LEGACY_LLM_INDEX = 2

__all__ = [
    "CriticAction",
    "CriticScope",
    "CriticResult",
    "CritiqueContext",
    "critique",
    "extract_entity_conclusion",
]


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
    llm: CompletionClient | str | None = None,
    *rest: object,
    allow_llm: bool = True,
    sub_question: str = "",
    evidence: list[Candidate] | None = None,
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
    llm: CompletionClient | str | None,
    rest: tuple[object, ...],
    *,
    sub_question: str,
    evidence: list[Candidate] | None,
    explored_paths: list[str] | None,
    hop: int,
    max_hops: int,
    remaining_subquestions: int,
    excluded_hypotheses: list[str] | None,
) -> tuple[CritiqueContext, CompletionClient | None]:
    if isinstance(question, CritiqueContext):
        return question, _client(llm)
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
    return ctx, _client(llm)


def _legacy_positional(
    question: str,
    sub_question: str,
    rest: tuple[object, ...],
    *,
    hop: int,
    max_hops: int,
    remaining_subquestions: int,
    excluded_hypotheses: list[str] | None,
) -> tuple[CritiqueContext, CompletionClient | None]:
    """Unpack critique(q, sq, evidence, paths, llm, ...)."""
    evidence = _EVIDENCE_LIST.validate_python(rest[_LEGACY_EVIDENCE_INDEX] or []) if rest else []
    paths = (
        _PATH_LIST.validate_python(rest[_LEGACY_PATHS_INDEX] or [])
        if len(rest) > _LEGACY_PATHS_INDEX
        else []
    )
    resolved_llm = _client(rest[_LEGACY_LLM_INDEX]) if len(rest) > _LEGACY_LLM_INDEX else None
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


def _client(value: object) -> CompletionClient | None:
    if value is None or isinstance(value, CompletionClient):
        return value
    raise TypeError("Critic requires a CompletionClient or None")


def _critique_impl(
    ctx: CritiqueContext,
    llm: CompletionClient | None,
    *,
    allow_llm: bool,
) -> CriticResult:
    from agentic_graphrag.agent.critic_guard import force_incomplete_multihop
    from agentic_graphrag.agent.critic_tiered import normalize_mode, tiered_critique

    if not ctx.evidence:
        return _critique_no_evidence(ctx)
    mode = normalize_mode(ctx.critic_mode)
    if not allow_llm or llm is None or mode is CriticMode.OFFLINE:
        result = offline_critique(ctx)
    elif mode is CriticMode.TIERED:
        result, tier = tiered_critique(ctx, lambda c: _llm_critique(c, llm))
        result.rationale = f"[critic_tier={tier}] {result.rationale}".strip()
    else:
        result = _llm_critique(ctx, llm)
    return force_incomplete_multihop(ctx, result)


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


def _llm_critique(ctx: CritiqueContext, llm: CompletionClient) -> CriticResult:
    prompt = load_prompt(PromptName.CRITIC)
    evidence_list = "\n".join(
        f"[{c.id}] {c.content[:_EVIDENCE_CONTENT_CHARS]}"
        for c in ctx.evidence[:_EVIDENCE_PROMPT_CAP]
    )
    system, user = _split(
        prompt.format(
            question=ctx.question,
            sub_question=ctx.sub_question,
            evidence_list=evidence_list or "(none)",
            explored_paths="; ".join(ctx.explored_paths[:_EVIDENCE_PROMPT_CAP]) or "(none)",
        )
    )
    user = (
        user + "\n\nJudge BOTH levels: set sub_answered if the sub-question is covered; "
        "set global_answered only if the original question can be fully answered. "
        "If sub is done but global is not, action=next_hop with a new sub-question "
        "(or rewrite the current one). Prefer rewrite when the sub-question wording "
        "is the bottleneck."
    )
    # LIGHT tier for critic: large latency win on live multi-hop (P95 path).
    raw = complete_structured(
        llm,
        [
            Message(role=MessageRole.SYSTEM, content=system),
            Message(role=MessageRole.USER, content=user),
        ],
        CriticResult,
        tier=Tier.LIGHT,
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
    if PROMPT_SYSTEM_HEADER in text and PROMPT_USER_HEADER in text:
        parts = text.split(PROMPT_USER_HEADER, 1)
        return parts[0].replace(PROMPT_SYSTEM_HEADER, "", 1).strip(), parts[1].strip()
    return "You are a critic.", text
