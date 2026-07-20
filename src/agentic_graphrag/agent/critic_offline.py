"""Offline critic predicates and entity-conclusion extractors."""

from __future__ import annotations

from dataclasses import dataclass

from agentic_graphrag.agent.options import CritiqueContext
from agentic_graphrag.retrieval.contracts import Candidate

_EVIDENCE_ID_CAP = 8
_PARTIAL_CONTENT_MAX_LEN = 80
_MIN_TEXT_EVIDENCE = 3

_PARENT_RELS = frozenset({"PARENT_OF", "OWNS"})
_SUBSIDIARY_RELS = frozenset({"SUBSIDIARY_OF"})
# Person as subject of the edge (CEO / employment subject).
_PERSON_SUBJECT_RELS = frozenset({"CEO_OF", "WORKED_AT", "EMPLOYED_BY"})
# Relations that answer "where does X work" with the company (tail).
_WORK_RELS = frozenset({"WORKED_AT", "EMPLOYED_BY"})
# Content-parse path: employment aliases kept separate from CEO-only shortcuts.
_PERSON_RELS_PARSE = frozenset({"CEO_OF", "WORKED_AT"})


# Late import of CriticResult types to avoid circulars at module load of critic.
def _results():
    from agentic_graphrag.agent.critic import CriticAction, CriticResult, CriticScope

    return CriticAction, CriticResult, CriticScope


@dataclass(frozen=True)
class _Endpoints:
    head: str
    tail: str
    rel: str
    query_entity: str


def extract_entity_conclusion(
    sub_question: str,
    evidence: list[Candidate],
) -> str | None:
    """Clean entity name for placeholder materialization (not full edge text).

    Scans all graph hits and prefers edges whose relation matches the
    sub-question (so hop-1 PARENT_OF edges do not poison hop-2 CEO conclusions).
    """
    graph_hits = [c for c in evidence if c.is_graph()]
    if not graph_hits:
        return None

    ranked = sorted(
        enumerate(graph_hits),
        key=lambda it: (_hit_relevance(sub_question, it[1]), it[0]),
        reverse=True,
    )
    for _idx, hit in ranked:
        conclusion = _conclusion_from_structured(sub_question, hit)
        if conclusion is not None:
            return conclusion
        conclusion = _conclusion_from_edge_parse(sub_question, hit)
        if conclusion is not None:
            return conclusion
    return None


def _hit_relevance(sub_question: str, c: Candidate) -> int:
    """Higher = better match between graph edge and sub-question intent."""
    ql = (sub_question or "").lower()
    ep = _endpoints_from_candidate(c)
    rel = ep.rel if ep else _rel_from_content(c.content)
    score = 0
    if _asks_parent(ql) and rel in _PARENT_RELS | _SUBSIDIARY_RELS:
        score += 10
    if _asks_ceo(ql) and rel == "CEO_OF":
        score += 10
    if _asks_work(ql) and rel in _WORK_RELS:
        score += 10
    if _asks_subsidiary(ql) and rel in _SUBSIDIARY_RELS | _PARENT_RELS:
        score += 10
    if ep is not None:
        score += 2
        qe = (ep.query_entity or "").lower()
        if qe and (qe in ep.head.lower() or qe in ep.tail.lower()):
            score += 3
        # Prefer edges whose endpoints appear in the sub-question text.
        for side in (ep.head, ep.tail):
            if side and side.lower() in ql:
                score += 2
    return score


def _rel_from_content(content: str) -> str:
    # "A -[REL]-> B"
    try:
        left = content.split("-[", 1)[1]
        return left.split("]->", 1)[0].strip().upper()
    except (IndexError, AttributeError):
        return ""


def _endpoints_from_candidate(c: Candidate) -> _Endpoints | None:
    st = c.structured or {}
    head = str(st.get("head") or "")
    tail = str(st.get("tail") or st.get("neighbor") or "")
    if not head and not tail:
        return None
    return _Endpoints(
        head=head,
        tail=tail,
        rel=str(st.get("relation") or "").upper(),
        query_entity=str(st.get("query_entity") or ""),
    )


def _conclusion_from_structured(sub_question: str, c: Candidate) -> str | None:
    ep = _endpoints_from_candidate(c)
    if ep is None:
        return None
    ql = (sub_question or "").lower()
    # Strong cues: mismatched relations return None so the caller tries next hit
    # (do not fall back to arbitrary endpoint on a PARENT_OF edge for a CEO ask).
    if _asks_parent(ql) or _asks_work(ql) or _asks_ceo(ql) or _asks_subsidiary(ql):
        return _pick_by_question_cue(ql, ep)
    return _prefer_non_query_side(ep)


def _asks_parent(ql: str) -> bool:
    return "parent" in ql


def _asks_ceo(ql: str) -> bool:
    return "ceo" in ql or "who is" in ql or ql.strip().startswith("who ")


def _asks_work(ql: str) -> bool:
    return any(
        k in ql
        for k in (
            "work",
            "worked",
            "employ",
            "previously work",
            "companies did",
            "where does",
            "where did",
        )
    )


def _asks_subsidiary(ql: str) -> bool:
    return "subsidiary" in ql or "child" in ql


def _pick_by_question_cue(ql: str, ep: _Endpoints) -> str | None:
    if _asks_parent(ql):
        return _pick_parent(ep)
    if _asks_work(ql):
        return _pick_workplace(ep)
    if _asks_ceo(ql):
        return _pick_person(ep)
    if _asks_subsidiary(ql):
        return ep.tail or ep.head
    return None


def _pick_parent(ep: _Endpoints) -> str:
    if ep.rel in _PARENT_RELS:
        return ep.head or ep.tail
    if ep.rel in _SUBSIDIARY_RELS:
        return ep.tail or ep.head
    return ep.head or ep.tail


def _pick_person(ep: _Endpoints) -> str | None:
    """Pick person subject for CEO-style questions; skip mismatched relations."""
    if ep.rel == "CEO_OF":
        return ep.head or ep.tail
    # Generic "who is …" may still land on employment edges.
    if ep.rel in _WORK_RELS:
        return ep.head or ep.tail
    # Mismatched relation (e.g. PARENT_OF while asking for CEO) — skip hit.
    if ep.rel:
        return None
    return ep.head or ep.tail


def _pick_workplace(ep: _Endpoints) -> str | None:
    """Company/org side of employment edges."""
    if ep.rel in _WORK_RELS:
        return ep.tail or ep.head
    if ep.rel == "CEO_OF":
        # Wrong edge type for a workplace question.
        return None
    return ep.tail or ep.head


def _prefer_non_query_side(ep: _Endpoints) -> str:
    q = ep.query_entity
    if q and ep.head and q.lower() in ep.head.lower():
        return ep.tail or ep.head
    if q and ep.tail and q.lower() in ep.tail.lower():
        return ep.head or ep.tail
    return ep.tail or ep.head


def _conclusion_from_edge_parse(sub_question: str, c: Candidate) -> str | None:
    try:
        from agentic_graphrag.generation.offline_edges import parse_edges

        edges = parse_edges([c.content])
        if not edges:
            return None
        h, r, t = edges[0]
        ql = (sub_question or "").lower()
        if _asks_parent(ql):
            return h if r in _PARENT_RELS else t
        if _asks_work(ql):
            if r in _WORK_RELS or r == "EMPLOYED_BY":
                return t
            if r == "CEO_OF":
                return None
            return t
        if _asks_ceo(ql):
            if r == "CEO_OF" or r in _PERSON_RELS_PARSE:
                return h
            return None
        if r in _PERSON_RELS_PARSE and not _asks_work(ql):
            return h
        return t
    except Exception:
        return None


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
