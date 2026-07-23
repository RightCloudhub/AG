"""Offline critic predicates and entity-conclusion extractors."""

from __future__ import annotations

from dataclasses import dataclass

from agentic_graphrag.agent.critic_offline_rel import (
    asks_work,
    conclusion_from_edge_parse,
    conclusion_from_structured,
    hit_relevance,
)
from agentic_graphrag.agent.options import CritiqueContext
from agentic_graphrag.retrieval.contracts import Candidate

_EVIDENCE_ID_CAP = 8
_PARTIAL_CONTENT_MAX_LEN = 80
_MIN_TEXT_EVIDENCE = 3


def _results():
    from agentic_graphrag.agent.critic import CriticAction, CriticResult, CriticScope

    return CriticAction, CriticResult, CriticScope


def extract_entity_conclusion(
    sub_question: str,
    evidence: list[Candidate],
) -> str | None:
    """Clean entity name for placeholder materialization (not full edge text)."""
    graph_hits = [c for c in evidence if c.is_graph()]
    if not graph_hits:
        return None
    # Prefer higher relevance; break ties by earlier retrieval rank (stable, deterministic).
    ranked = sorted(
        enumerate(graph_hits),
        key=lambda it: (-hit_relevance(sub_question, it[1]), it[0]),
    )
    ql = (sub_question or "").lower()
    if asks_work(ql):
        return _aggregate_workplaces(sub_question, ranked)
    for _idx, hit in ranked:
        conclusion = conclusion_from_structured(sub_question, hit)
        if conclusion is not None:
            return conclusion
        conclusion = conclusion_from_edge_parse(sub_question, hit)
        if conclusion is not None:
            return conclusion
    return None


def _aggregate_workplaces(
    sub_question: str,
    ranked: list[tuple[int, Candidate]],
) -> str | None:
    found: list[str] = []
    seen: set[str] = set()
    for _idx, hit in ranked:
        if hit_relevance(sub_question, hit) < 10:
            continue
        for fn in (conclusion_from_structured, conclusion_from_edge_parse):
            val = fn(sub_question, hit)
            if not val:
                continue
            key = val.lower()
            if key in seen:
                break
            seen.add(key)
            found.append(val)
            break
    if not found:
        return None
    return " and ".join(found)


def _offline_partial(sub_question: str, evidence: list[Candidate]) -> str | None:
    partial = extract_entity_conclusion(sub_question, evidence)
    if partial is not None or not evidence:
        return partial
    graph_hits = [c for c in evidence if c.is_graph()]
    raw = graph_hits[0].content if graph_hits else evidence[0].content
    if raw and "-[" not in raw and len(raw) < _PARTIAL_CONTENT_MAX_LEN:
        return raw
    return None


@dataclass(frozen=True)
class _OfflineBits:
    eids: list[str]
    partial: str | None
    graph_hits: list[Candidate]
    evidence: list[Candidate]
    ctx: CritiqueContext


def offline_critique(ctx: CritiqueContext):
    CriticAction, CriticResult, CriticScope = _results()
    evidence = list(ctx.evidence)
    bits = _OfflineBits(
        eids=[c.id for c in evidence[:_EVIDENCE_ID_CAP]],
        partial=_offline_partial(ctx.sub_question, evidence),
        graph_hits=[c for c in evidence if c.is_graph()],
        evidence=evidence,
        ctx=ctx,
    )
    for pred in (
        _offline_planned_remaining,
        _offline_graph_sufficient,
        _offline_text_or_limit,
        _offline_rewrite_excluded,
    ):
        hit = pred(bits)
        if hit is not None:
            return hit
    return CriticResult(
        action=CriticAction.NEXT_HOP,
        scope=CriticScope.SUB_QUESTION,
        rationale="offline: need more evidence",
        new_sub_question=ctx.sub_question,
        evidence_ids=bits.eids,
        partial_answer=bits.partial,
        sub_answered=False,
        global_answered=False,
    )


def _offline_planned_remaining(bits: _OfflineBits):
    CriticAction, CriticResult, CriticScope = _results()
    ctx = bits.ctx
    if ctx.remaining_subquestions <= 0 or ctx.hop >= ctx.max_hops:
        return None
    return CriticResult(
        action=CriticAction.SUFFICIENT,
        scope=CriticScope.SUB_QUESTION,
        rationale="offline: sub-question done; more planned nodes remain",
        evidence_ids=bits.eids,
        partial_answer=bits.partial,
        sub_answered=True,
        global_answered=False,
    )


def _offline_graph_sufficient(bits: _OfflineBits):
    CriticAction, CriticResult, CriticScope = _results()
    if not bits.graph_hits:
        return None
    return CriticResult(
        action=CriticAction.SUFFICIENT,
        scope=CriticScope.GLOBAL,
        rationale="offline: graph evidence available for original question",
        evidence_ids=bits.eids,
        partial_answer=bits.partial,
        sub_answered=True,
        global_answered=True,
    )


def _offline_text_or_limit(bits: _OfflineBits):
    CriticAction, CriticResult, CriticScope = _results()
    ctx, evidence = bits.ctx, bits.evidence
    if len(evidence) < _MIN_TEXT_EVIDENCE and ctx.hop < ctx.max_hops:
        return None
    return CriticResult(
        action=CriticAction.SUFFICIENT if evidence else CriticAction.GIVE_UP,
        scope=CriticScope.GLOBAL,
        rationale="offline: text evidence only or hop limit",
        evidence_ids=bits.eids,
        partial_answer=bits.partial,
        sub_answered=bool(evidence),
        global_answered=bool(evidence),
    )


def _offline_rewrite_excluded(bits: _OfflineBits):
    CriticAction, CriticResult, CriticScope = _results()
    ctx = bits.ctx
    excluded = ctx.excluded_hypotheses
    if not excluded:
        return None
    if not any(h.lower() in ctx.sub_question.lower() for h in excluded if h):
        return None
    return CriticResult(
        action=CriticAction.REWRITE,
        scope=CriticScope.SUB_QUESTION,
        rationale="offline: current sub-question hits excluded hypothesis",
        new_sub_question=ctx.question,
        evidence_ids=bits.eids,
        partial_answer=bits.partial,
        sub_answered=False,
        global_answered=False,
    )
