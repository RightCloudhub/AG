"""Relation-aware conclusion pickers for offline critic entity extraction."""

from __future__ import annotations

from dataclasses import dataclass

from agentic_graphrag.retrieval.contracts import Candidate

_PARENT_RELS = frozenset({"PARENT_OF", "OWNS"})
_SUBSIDIARY_RELS = frozenset({"SUBSIDIARY_OF"})
_WORK_RELS = frozenset({"WORKED_AT", "EMPLOYED_BY"})
_PERSON_RELS_PARSE = frozenset({"CEO_OF", "WORKED_AT"})
_WORK_KEYS = (
    "work",
    "worked",
    "employ",
    "previously work",
    "companies did",
    "where does",
    "where did",
)


@dataclass(frozen=True)
class Endpoints:
    head: str
    tail: str
    rel: str
    query_entity: str


def asks_parent(ql: str) -> bool:
    return "parent" in ql


def asks_ceo(ql: str) -> bool:
    return "ceo" in ql or "who is" in ql or ql.strip().startswith("who ")


def asks_work(ql: str) -> bool:
    return any(k in ql for k in _WORK_KEYS)


def asks_subsidiary(ql: str) -> bool:
    return "subsidiary" in ql or "child" in ql


def rel_from_content(content: str) -> str:
    try:
        left = content.split("-[", 1)[1]
        return left.split("]->", 1)[0].strip().upper()
    except (IndexError, AttributeError):
        return ""


def endpoints_from_candidate(c: Candidate) -> Endpoints | None:
    st = c.structured or {}
    head = str(st.get("head") or "")
    tail = str(st.get("tail") or st.get("neighbor") or "")
    if not head and not tail:
        return None
    return Endpoints(
        head=head,
        tail=tail,
        rel=str(st.get("relation") or "").upper(),
        query_entity=str(st.get("query_entity") or ""),
    )


def hit_relevance(sub_question: str, c: Candidate) -> int:
    """Higher = better match between graph edge and sub-question intent."""
    ql = (sub_question or "").lower()
    ep = endpoints_from_candidate(c)
    rel = ep.rel if ep else rel_from_content(c.content)
    score = _rel_intent_score(ql, rel, ep)
    if ep is not None:
        score += _endpoint_bonus(ql, ep)
    return score


def _rel_intent_score(ql: str, rel: str, ep: Endpoints | None) -> int:
    score = _parent_sub_score(ql, rel) + _work_score(ql, rel, ep)
    if asks_ceo(ql) and rel == "CEO_OF":
        score += 10 + _ceo_company_bonus(ql, ep)
    return score


def _parent_sub_score(ql: str, rel: str) -> int:
    org_rels = _PARENT_RELS | _SUBSIDIARY_RELS
    if asks_parent(ql) and rel in org_rels:
        return 10
    if asks_subsidiary(ql) and rel in org_rels:
        return 10
    return 0


def _work_score(ql: str, rel: str, ep: Endpoints | None) -> int:
    if not (asks_work(ql) and rel in _WORK_RELS):
        return 0
    score = 10
    if ep and ep.head and ep.head.lower() in ql:
        score += 10
    return score


def _ceo_company_bonus(ql: str, ep: Endpoints | None) -> int:
    if not ep or not ep.tail:
        return 0
    return 15 if ep.tail.lower() in ql else -8


def _endpoint_bonus(ql: str, ep: Endpoints) -> int:
    score = 2
    qe = (ep.query_entity or "").lower()
    if qe and (qe in ep.head.lower() or qe in ep.tail.lower()):
        score += 3
    for side in (ep.head, ep.tail):
        if side and side.lower() in ql:
            score += 2
    return score


def conclusion_from_structured(sub_question: str, c: Candidate) -> str | None:
    ep = endpoints_from_candidate(c)
    if ep is None:
        return None
    ql = (sub_question or "").lower()
    if asks_parent(ql) or asks_work(ql) or asks_ceo(ql) or asks_subsidiary(ql):
        return pick_by_question_cue(ql, ep)
    return prefer_non_query_side(ep)


def pick_by_question_cue(ql: str, ep: Endpoints) -> str | None:
    if asks_parent(ql):
        return pick_parent(ep)
    if asks_work(ql):
        return pick_workplace(ep)
    if asks_ceo(ql):
        return pick_person(ep, ql)
    if asks_subsidiary(ql):
        return pick_subsidiary_answer(ql, ep)
    return None


def pick_subsidiary_answer(ql: str, ep: Endpoints) -> str | None:
    if ep.rel in _PARENT_RELS:
        return _yes_or_side(ql, ep.tail, ep.head, prefer=ep.head)
    if ep.rel in _SUBSIDIARY_RELS:
        return _yes_or_side(ql, ep.head, ep.tail, prefer=ep.tail)
    return None


def _yes_or_side(ql: str, a: str, b: str, *, prefer: str) -> str:
    al, bl = (a or "").lower(), (b or "").lower()
    if al and bl and al.split()[0] in ql and bl.split()[0] in ql:
        return "Yes"
    return prefer or a or b


def pick_parent(ep: Endpoints) -> str:
    if ep.rel in _PARENT_RELS:
        return ep.head or ep.tail
    if ep.rel in _SUBSIDIARY_RELS:
        return ep.tail or ep.head
    return ep.head or ep.tail


def pick_person(ep: Endpoints, ql: str = "") -> str | None:
    if ep.rel == "CEO_OF":
        return _ceo_person(ep, ql)
    if ep.rel in _WORK_RELS:
        return ep.head or ep.tail
    if ep.rel:
        return None
    return ep.head or ep.tail


def _ceo_person(ep: Endpoints, ql: str) -> str | None:
    tail_l = (ep.tail or "").lower()
    qe_l = (ep.query_entity or "").lower()
    if not _ceo_company_ok(ql, tail_l, qe_l):
        return None
    return ep.head or ep.tail


def _ceo_company_ok(ql: str, tail_l: str, qe_l: str) -> bool:
    """Reject CEO edges whose company does not match the sub-question/seed."""
    in_q = bool(ql and tail_l and tail_l in ql)
    ctx = (ql, tail_l, qe_l, in_q)
    if _ceo_reject_missing_company(ctx):
        return False
    return not _ceo_reject_seed_mismatch(ctx)


def _ceo_reject_missing_company(ctx: tuple[str, str, str, bool]) -> bool:
    ql, tail_l, qe_l, in_q = ctx
    if not (ql and tail_l) or in_q:
        return False
    return not qe_l or qe_l not in tail_l


def _ceo_reject_seed_mismatch(ctx: tuple[str, str, str, bool]) -> bool:
    _ql, tail_l, qe_l, in_q = ctx
    if not qe_l or not tail_l or in_q:
        return False
    return qe_l not in tail_l and tail_l not in qe_l


def pick_workplace(ep: Endpoints) -> str | None:
    if ep.rel in _WORK_RELS:
        return ep.tail or ep.head
    if ep.rel == "CEO_OF":
        return None
    return ep.tail or ep.head


def prefer_non_query_side(ep: Endpoints) -> str:
    q = ep.query_entity
    if q and ep.head and q.lower() in ep.head.lower():
        return ep.tail or ep.head
    if q and ep.tail and q.lower() in ep.tail.lower():
        return ep.head or ep.tail
    return ep.tail or ep.head


def conclusion_from_edge_parse(sub_question: str, c: Candidate) -> str | None:
    try:
        from agentic_graphrag.generation.offline_edges import parse_edges

        edges = parse_edges([c.content])
        if not edges:
            return None
        return _parse_edge_pick(sub_question, edges[0])
    except Exception:
        return None


def _parse_edge_pick(sub_question: str, edge: tuple[str, str, str]) -> str | None:
    head, rel, tail = edge
    ql = (sub_question or "").lower()
    if asks_parent(ql):
        return head if rel in _PARENT_RELS else tail
    if asks_work(ql):
        return _parse_work_pick(rel, tail)
    if asks_ceo(ql):
        return _parse_ceo_pick(ql, rel, (head, tail))
    if rel in _PERSON_RELS_PARSE:
        return head
    return tail


def _parse_work_pick(rel: str, tail: str) -> str | None:
    if rel in _WORK_RELS or rel == "EMPLOYED_BY":
        return tail
    return None if rel == "CEO_OF" else tail


def _parse_ceo_pick(ql: str, rel: str, ends: tuple[str, str]) -> str | None:
    head, tail = ends
    if rel == "CEO_OF":
        return None if tail and tail.lower() not in ql else head
    return head if rel in _PERSON_RELS_PARSE else None
