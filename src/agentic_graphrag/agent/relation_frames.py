"""Schema-level relation frames shared by the offline planner and critic.

A *frame* is one relational step a question can ask about ("the parent company
of X", "the CEO of X", "the producer of X"). Each frame knows:

* the surface forms that name it in a question (noun phrases and verb phrases),
* which graph relations answer it and on which side of the edge the answer sits
  relative to the anchor entity X,
* how to phrase a standalone sub-question for it.

Frames are defined against the relation vocabulary of
``configs/schema/domain_v0.yaml`` (PARENT_OF, CEO_OF, PRODUCES, ...), never
against specific entities, so they generalize to any corpus that uses the schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from agentic_graphrag.core.evidence import Candidate


class Side(StrEnum):
    """Where the answer sits on an edge whose other end is the anchor."""

    HEAD = "head"
    TAIL = "tail"
    EITHER = "either"


@dataclass(frozen=True)
class RelationFrame:
    key: str
    nouns: tuple[str, ...]
    rels: tuple[tuple[str, Side], ...]
    template: str
    multi: bool = False


FRAMES: tuple[RelationFrame, ...] = (
    RelationFrame(
        "parent",
        ("parent company", "parent firm", "parent", "holding company", "owner"),
        (("PARENT_OF", Side.HEAD), ("SUBSIDIARY_OF", Side.TAIL), ("OWNS", Side.HEAD)),
        "What is the parent company of {x}?",
    ),
    RelationFrame(
        "subsidiary",
        ("subsidiaries", "subsidiary", "child company"),
        (("PARENT_OF", Side.TAIL), ("SUBSIDIARY_OF", Side.HEAD), ("OWNS", Side.TAIL)),
        "What are the subsidiaries of {x}?",
        multi=True,
    ),
    RelationFrame(
        "ceo",
        ("chief executive officer", "chief executive", "ceo"),
        (("CEO_OF", Side.HEAD),),
        "Who is the CEO of {x}?",
    ),
    RelationFrame(
        "producer",
        ("producer", "manufacturer", "maker"),
        (("PRODUCES", Side.HEAD),),
        "Which company produces {x}?",
    ),
    RelationFrame(
        "competitor",
        ("competitors", "competitor", "rivals", "rival"),
        (("COMPETES_WITH", Side.EITHER),),
        "Which company competes with {x}?",
        multi=True,
    ),
    RelationFrame(
        "supplier",
        ("suppliers", "supplier", "vendors", "vendor"),
        (("SUPPLIES_FOR", Side.HEAD), ("SUPPLIES", Side.HEAD)),
        "Which companies supply {x}?",
        multi=True,
    ),
    RelationFrame(
        "founder",
        ("founders", "founder"),
        (("FOUNDED", Side.HEAD),),
        "Who founded {x}?",
    ),
    RelationFrame(
        "employer",
        ("previous employers", "previous employer", "former employer", "prior employer"),
        (("WORKED_AT", Side.TAIL),),
        "Which companies did {x} previously work at?",
        multi=True,
    ),
    RelationFrame(
        "hq",
        ("headquarters city", "headquarters"),
        (("LOCATED_IN", Side.TAIL),),
        "Where is {x} headquartered?",
    ),
    RelationFrame(
        "event",
        ("events participated in",),
        (("PARTICIPATED_IN", Side.TAIL),),
        "Which event did {x} participate in?",
        multi=True,
    ),
)

_BY_KEY = {f.key: f for f in FRAMES}

# Verb / clause surface forms → frame key. Each pattern consumes the phrase that
# introduces the anchor, so "the company that produces X" reads like "the
# producer of X". Applied before noun parsing (see planner_chain.normalize_chain).
CLAUSE_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(?:company|firm|business) (?:that|which) (?:produces|makes|manufactures)\b"),
        "producer of",
    ),
    (re.compile(r"\b(?:company|firm|business) (?:that|which) competes with\b"), "competitor of"),
    (
        re.compile(r"\b(?:company|firm|business) (?:that|which) (?:owns|controls)\b"),
        "parent company of",
    ),
    (
        re.compile(r"\b(?:company|firm|business) (?:that|which) supplies(?: for| to)?\b"),
        "supplier of",
    ),
)

# Leading verb frames: "Which company competes with the <chain>?" etc.
LEAD_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^which (?:company|companies|firm) (?:competes|compete) with (?:the )?"),
        "what is the competitor of the ",
    ),
    (re.compile(r"^who founded (?:the )?"), "who is the founder of the "),
    (
        re.compile(r"^which (?:company|companies|firm) (?:owns|own|controls) (?:the )?"),
        "what is the parent company of the ",
    ),
    (
        re.compile(r"^which (?:company|companies|firm) (?:produces|makes) (?:the )?"),
        "what is the producer of the ",
    ),
)


def frame(key: str) -> RelationFrame:
    return _BY_KEY[key]


def detect_frame(text: str) -> RelationFrame | None:
    """Return the frame a (sub-)question asks about, by its first matching cue."""
    tl = f" {(text or '').lower()} "
    for verb, key in _VERB_CUES:
        if verb in tl:
            return _BY_KEY[key]
    best: tuple[int, RelationFrame] | None = None
    for fr in FRAMES:
        pos = _first_noun_pos(tl, fr)
        if pos >= 0 and (best is None or pos < best[0]):
            best = (pos, fr)
    return best[1] if best else None


_VERB_CUES: tuple[tuple[str, str], ...] = (
    (" previously work", "employer"),
    (" headquartered", "hq"),
    (" participate in", "event"),
    (" participated in", "event"),
    (" competes with ", "competitor"),
    (" compete with ", "competitor"),
    (" produces ", "producer"),
    (" founded ", "founder"),
    (" owns ", "parent"),
    (" supply ", "supplier"),
    (" supplies ", "supplier"),
)


def _first_noun_pos(tl: str, fr: RelationFrame) -> int:
    positions = [m.start() for n in fr.nouns for m in re.finditer(rf"\b{re.escape(n)}\b", tl)]
    return min(positions) if positions else -1


@dataclass(frozen=True)
class FrameMatch:
    answer: str


def match_edge(fr: RelationFrame, anchor: str, c: Candidate) -> FrameMatch | None:
    """Answer endpoint when ``c`` is an edge of ``fr`` with ``anchor`` on the right side."""
    edge = _edge_of(c)
    if edge is None or not anchor:
        return None
    rel, head, tail = edge
    side = dict(fr.rels).get(rel)
    if side is None:
        return None
    answer = _answer_side(side, _norm(anchor), (head, tail))
    return FrameMatch(answer=answer) if answer is not None else None


def _edge_of(c: Candidate) -> tuple[str, str, str] | None:
    st = c.structured or {}
    rel = str(st.get("relation") or "").upper()
    head, tail = str(st.get("head") or ""), str(st.get("tail") or "")
    if not (rel and head and tail):
        return None
    return rel, head, tail


def _answer_side(side: Side, anchor_n: str, ends: tuple[str, str]) -> str | None:
    head, tail = ends
    head_is, tail_is = _norm(head) == anchor_n, _norm(tail) == anchor_n
    if side in (Side.HEAD, Side.EITHER) and tail_is and not head_is:
        return head
    if side in (Side.TAIL, Side.EITHER) and head_is and not tail_is:
        return tail
    return None


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def resolve_frame_answers(sub_question: str, evidence: list[Candidate]) -> list[str]:
    """Direction-checked answers for a frame sub-question over graph evidence.

    Only edges that touch an anchor (an entity named in the sub-question) on the
    frame's anchor side count, so ``Polaris Cloud -[PARENT_OF]-> Lattice AI`` can
    never answer "parent company of Polaris Cloud". Single-valued frames keep the
    best-ranked answer per anchor; multi-valued frames keep all. Returns ``[]``
    when no frame or anchor is recognized (callers fall back to older heuristics).
    """
    graph = [c for c in evidence if c.is_graph()]
    anchors = frame_anchors(sub_question, graph)
    if not anchors:
        return []
    fr = detect_frame(_strip_names(sub_question, anchors))
    if fr is None:
        return []
    found: list[str] = []
    for anchor in anchors:
        per_anchor = _answers_for_anchor(fr, anchor, graph)
        if not fr.multi:
            per_anchor = per_anchor[:1]
        found.extend(a for a in per_anchor if a.lower() not in {f.lower() for f in found})
    return found


def frame_anchors(sub_question: str, graph: list[Candidate]) -> list[str]:
    """Entity names from graph hits that the sub-question mentions, in text order."""
    ql = (sub_question or "").lower()
    names: dict[str, tuple[int, str]] = {}
    for c in graph:
        st = c.structured or {}
        for key in ("query_entity", "head", "tail"):
            name = str(st.get(key) or "").strip()
            pos = _mention_pos(ql, name)
            if pos >= 0 and _norm(name) not in names:
                names[_norm(name)] = (pos, name)
    ordered = sorted(names.values())
    return _drop_nested([n for _, n in ordered])


def _mention_pos(ql: str, name: str) -> int:
    if len(name) < 2:
        return -1
    m = re.search(rf"(?<!\w){re.escape(name.lower())}(?!\w)", ql)
    return m.start() if m else -1


def _drop_nested(names: list[str]) -> list[str]:
    """Drop names contained in a longer mentioned name (``Helix`` inside ``Helix Compute``)."""
    low = [n.lower() for n in names]
    return [
        n for n, nl in zip(names, low, strict=True) if not any(nl != o and nl in o for o in low)
    ]


def _strip_names(text: str, names: list[str]) -> str:
    out = text
    for n in names:
        out = re.sub(re.escape(n), " ", out, flags=re.IGNORECASE)
    return out


def _answers_for_anchor(fr: RelationFrame, anchor: str, graph: list[Candidate]) -> list[str]:
    out: list[str] = []
    for c in graph:
        hit = match_edge(fr, anchor, c)
        if hit is None or hit.answer.lower() in {o.lower() for o in out}:
            continue
        out.append(hit.answer)
    return out
