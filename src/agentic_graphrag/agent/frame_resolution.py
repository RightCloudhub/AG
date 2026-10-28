"""Direction-checked answer resolution for frame sub-questions.

Given a sub-question that is exactly one relation frame applied to its anchor(s)
(see :mod:`agentic_graphrag.agent.relation_frames`), keep only graph edges that
touch an anchor on the frame's anchor side and return the opposite endpoints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentic_graphrag.agent.relation_frames import FRAMES, RelationFrame, Side
from agentic_graphrag.core.evidence import Candidate


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


def resolve_frame_answers(sub_question: str, evidence: list[Candidate]) -> list[str] | None:
    """Direction-checked answers for a frame sub-question over graph evidence.

    Only edges that touch an anchor (an entity named in the sub-question) on the
    frame's anchor side count, so ``Polaris Cloud -[PARENT_OF]-> Lattice AI`` can
    never answer "parent company of Polaris Cloud". Single-valued frames keep the
    best-ranked answer per anchor; multi-valued frames keep all.

    Returns ``None`` when the sub-question is not a recognized frame shape (the
    caller may use other heuristics) and ``[]`` when it is one but no edge
    qualifies — an honest "not found" that must not be papered over.
    """
    graph = [c for c in evidence if c.is_graph()]
    anchors = frame_anchors(sub_question, graph)
    if not anchors:
        return None
    resolved = _frame_and_qualifier(sub_question, anchors)
    if resolved is None:
        return None
    fr = resolved[0]
    found: list[str] = []
    for anchor in anchors:
        per_anchor = _answers_for_anchor(resolved, anchor, graph)
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


_SLOT = "{x}"
_SLOT_RUN = re.compile(r"\{x\}(?:(?:,| and|, and) \{x\})+")
_NOUN_FORM = re.compile(r"^(?:who|what|which \w+) (?:is|are|was|were) the (?P<noun>.+) of \{x\}$")


def frame_for_subquestion(sub_question: str, anchors: list[str]) -> RelationFrame | None:
    """Frame of a sub-question that is exactly one frame applied to its anchor(s).

    Only canonical shapes count (a frame template, or "who/what is the <noun> of
    X"), so free-form questions whose direction is ambiguous ("Which CEO
    previously worked at X ...?" asks for the *subject* of WORKED_AT) never get
    a confidently wrong frame answer; they keep the legacy pickers.
    """
    shape = _slot_shape(sub_question, anchors)
    for fr in FRAMES:
        if shape == fr.template.lower().rstrip("?"):
            return fr
    m = _NOUN_FORM.match(shape)
    if m is None:
        return None
    noun = m.group("noun")
    return next((fr for fr in FRAMES if noun in fr.nouns), None)


def _slot_shape(text: str, names: list[str]) -> str:
    out = re.sub(r"\s+", " ", (text or "").strip().lower()).rstrip("?.! ")
    for n in sorted(names, key=len, reverse=True):
        out = re.sub(rf"(?<!\w){re.escape(n.lower())}(?!\w)", _SLOT, out)
    return _SLOT_RUN.sub(_SLOT, out)


def _frame_and_qualifier(sub_question: str, anchors: list[str]) -> tuple[RelationFrame, str] | None:
    fr = frame_for_subquestion(sub_question, anchors)
    return (fr, "") if fr is not None else qualified_frame(sub_question, anchors)


def qualified_frame(sub_question: str, anchors: list[str]) -> tuple[RelationFrame, str] | None:
    """``Which companies supply chips for {x}`` → (supplier frame, "chips")."""
    shape = _slot_shape(sub_question, anchors)
    for fr in FRAMES:
        if not fr.qualified:
            continue
        pat = re.escape(fr.qualified.lower().rstrip("?")).replace(r"\{q\}", "(?P<q>.+?)")
        m = re.fullmatch(pat.replace(r"\{x\}", re.escape(_SLOT)), shape)
        if m:
            return fr, m.group("q")
    return None


def _answers_for_anchor(
    resolved: tuple[RelationFrame, str], anchor: str, graph: list[Candidate]
) -> list[str]:
    fr, qualifier = resolved
    hits: list[tuple[str, Candidate]] = []
    for c in graph:
        hit = match_edge(fr, anchor, c)
        if hit is None or hit.answer.lower() in {a.lower() for a, _ in hits}:
            continue
        hits.append((hit.answer, c))
    if qualifier:
        # Keep edges whose source text names what is supplied ("chips", "power
        # modules"); when no edge's provenance mentions it, keep them all.
        narrowed = [(a, c) for a, c in hits if _mentions(qualifier, _edge_text(c))]
        hits = narrowed or hits
    return [a for a, _ in hits]


def _edge_text(c: Candidate) -> str:
    spans = [cite.span or "" for cite in c.citations]
    return " ".join([c.content or "", *spans]).lower()


def _mentions(qualifier: str, text: str) -> bool:
    """Every qualifier word appears (singular/plural-insensitive) in ``text``."""
    words = [_singular(w) for w in re.findall(r"[a-z0-9]+", qualifier.lower()) if len(w) > 2]
    return bool(words) and all(re.search(rf"\b{re.escape(w)}", text) for w in words)


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
