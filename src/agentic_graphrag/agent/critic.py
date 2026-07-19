"""Critic: evidence sufficiency and next-hop decisions (FR-AG-04 / P2-AG-02).

Two-level judgment:
- **sub_question**: can current evidence answer the active sub-question?
- **global**: can all evidence answer the original question?

Actions: sufficient | next_hop | rewrite | give_up
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from agentic_graphrag.config import load_prompt
from agentic_graphrag.llm.provider import LLMProvider, Message, Tier
from agentic_graphrag.llm.structured import complete_structured
from agentic_graphrag.retrieval.contracts import Candidate


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
    # True when sub-question is done but original question still open
    sub_answered: bool = False
    global_answered: bool = False


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
    excluded_hypotheses: list[str] | None = None,
) -> CriticResult:
    if not evidence:
        if hop >= max_hops:
            return CriticResult(
                action=CriticAction.GIVE_UP,
                scope=CriticScope.GLOBAL,
                rationale="no evidence and hop limit",
            )
        return CriticResult(
            action=CriticAction.NEXT_HOP,
            scope=CriticScope.SUB_QUESTION,
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
            excluded_hypotheses=excluded_hypotheses or [],
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
    # Enrich user message with two-level instruction (prompt file may be older)
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
    return _normalize_scope(raw, remaining_subquestions=remaining_subquestions)


def _normalize_scope(result: CriticResult, *, remaining_subquestions: int) -> CriticResult:
    """Infer scope flags when the model omits them."""
    if result.action == CriticAction.SUFFICIENT:
        result.sub_answered = True
        if remaining_subquestions > 0:
            # Chain still has planned work — treat as sub-level sufficient only
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


def extract_entity_conclusion(
    sub_question: str,
    evidence: list[Candidate],
) -> str | None:
    """Clean entity name for placeholder materialization (not full edge text).

    Avoids polluting ``{from:sqN}`` with strings like
    ``Apex Holdings -[PARENT_OF]-> BrightLink ...``.
    """
    graph_hits = [c for c in evidence if c.is_graph()]
    if not graph_hits:
        return None
    c = graph_hits[0]
    st = c.structured or {}
    ql = (sub_question or "").lower()
    head = str(st.get("head") or "")
    tail = str(st.get("tail") or st.get("neighbor") or "")
    rel = str(st.get("relation") or "").upper()

    if head or tail:
        if "parent" in ql:
            if rel in {"PARENT_OF", "OWNS"}:
                return head or tail
            if rel in {"SUBSIDIARY_OF"}:
                return tail or head
            return head or tail
        if "ceo" in ql or "who is" in ql or "who " in ql:
            if rel in {"CEO_OF", "WORKED_AT", "EMPLOYED_BY"}:
                return head or tail
            return head or tail
        if "subsidiary" in ql or "child" in ql:
            return tail or head
        # Default: prefer the non-query side
        q_ent = str(st.get("query_entity") or "")
        if q_ent and head and q_ent.lower() in head.lower():
            return tail or head
        if q_ent and tail and q_ent.lower() in tail.lower():
            return head or tail
        return tail or head

    # Parse edge content fallback
    try:
        from agentic_graphrag.generation.offline_edges import parse_edges

        edges = parse_edges([c.content])
        if edges:
            h, r, t = edges[0]
            if "parent" in ql:
                return h if r in {"PARENT_OF", "OWNS"} else t
            if "ceo" in ql or r in {"CEO_OF", "WORKED_AT"}:
                return h
            return t
    except Exception:
        pass
    return None


def _offline_critique(
    question: str,
    sub_question: str,
    evidence: list[Candidate],
    *,
    hop: int,
    max_hops: int,
    remaining_subquestions: int,
    excluded_hypotheses: list[str],
) -> CriticResult:
    graph_hits = [c for c in evidence if c.is_graph()]
    eids = [c.id for c in evidence[:8]]
    partial = extract_entity_conclusion(sub_question, evidence)
    if partial is None and evidence:
        # Last resort: short content only if it looks like a bare name
        raw = graph_hits[0].content if graph_hits else evidence[0].content
        if raw and "-[" not in raw and len(raw) < 80:
            partial = raw

    # Planned DAG still has unfinished nodes → sub-question sufficient, not global
    if remaining_subquestions > 0 and hop < max_hops:
        return CriticResult(
            action=CriticAction.SUFFICIENT,
            scope=CriticScope.SUB_QUESTION,
            rationale="offline: sub-question done; more planned nodes remain",
            evidence_ids=eids,
            partial_answer=partial,
            sub_answered=True,
            global_answered=False,
        )

    # Graph evidence present → enough for offline POC generation at global level
    if graph_hits:
        return CriticResult(
            action=CriticAction.SUFFICIENT,
            scope=CriticScope.GLOBAL,
            rationale="offline: graph evidence available for original question",
            evidence_ids=eids,
            partial_answer=partial,
            sub_answered=True,
            global_answered=True,
        )

    if len(evidence) >= 3 or hop >= max_hops:
        return CriticResult(
            action=CriticAction.SUFFICIENT if evidence else CriticAction.GIVE_UP,
            scope=CriticScope.GLOBAL,
            rationale="offline: text evidence only or hop limit",
            evidence_ids=eids,
            partial_answer=partial,
            sub_answered=bool(evidence),
            global_answered=bool(evidence),
        )

    # Suggest rewrite when excluded hypotheses blocked the current phrasing
    if excluded_hypotheses and any(
        h.lower() in sub_question.lower() for h in excluded_hypotheses if h
    ):
        return CriticResult(
            action=CriticAction.REWRITE,
            scope=CriticScope.SUB_QUESTION,
            rationale="offline: current sub-question hits excluded hypothesis",
            new_sub_question=question,
            evidence_ids=eids,
            partial_answer=partial,
            sub_answered=False,
            global_answered=False,
        )

    return CriticResult(
        action=CriticAction.NEXT_HOP,
        scope=CriticScope.SUB_QUESTION,
        rationale="offline: need more evidence",
        new_sub_question=sub_question,
        evidence_ids=eids,
        partial_answer=partial,
        sub_answered=False,
        global_answered=False,
    )


def _split(text: str) -> tuple[str, str]:
    if "# System" in text and "# User" in text:
        parts = text.split("# User", 1)
        return parts[0].replace("# System", "", 1).strip(), parts[1].strip()
    return "You are a critic.", text
