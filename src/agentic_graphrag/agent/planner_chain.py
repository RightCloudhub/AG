"""Compositional offline planning for nested relation chains.

Questions such as "Who is the CEO of the parent company of the producer of X?"
are a chain of relational frames applied to one anchor entity. Instead of one
hand-written matcher per template, this module parses the chain generically
(any depth, any order of frames in :mod:`agentic_graphrag.agent.relation_frames`)
and emits one sub-question per hop, innermost first, linked by ``{from:sqN}``.

Only fully parsed chains of two or more frames are planned here; anything else
falls through to the specific matchers in ``planner_patterns``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentic_graphrag.agent.plan_dag import SubQuestion
from agentic_graphrag.agent.relation_frames import (
    CLAUSE_REWRITES,
    FRAMES,
    LEAD_REWRITES,
    RelationFrame,
    frame,
)

if TYPE_CHECKING:
    from agentic_graphrag.agent.planner_patterns import PlanContext

_MIN_CHAIN = 2
_MAX_CHAIN = 5
_LEAD = re.compile(r"^(?:who|what|which(?: \w+)?) (?:is|are|was|were) (?:the )?")
_OF = re.compile(r"^ of (?:the )?")
# Trailing verb frames wrapping an inner chain: (pattern, frame key).
_OUTER: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"^which (?:companies|company|firms?) did (?:the )?(?P<inner>.+) "
            r"previously work (?:at|for)$"
        ),
        "employer",
    ),
    (re.compile(r"^where is (?:the )?(?P<inner>.+) headquartered$"), "hq"),
    (re.compile(r"^which events? did (?:the )?(?P<inner>.+) participate in$"), "event"),
)
_EMPLOYER_CLAUSE = re.compile(
    r"\b(?:company|firm|investment firm|investor|employer) (?:where|that|which) "
    r"(?P<x>.+?) previously worked(?: at| for)?\b"
)
_NOUNS: tuple[tuple[str, RelationFrame], ...] = tuple(
    sorted(((n, f) for f in FRAMES for n in f.nouns), key=lambda t: -len(t[0]))
)


@dataclass(frozen=True)
class ParsedChain:
    """Frames from innermost (applied to the anchor first) to outermost."""

    frames: tuple[RelationFrame, ...]
    anchor: str


def normalize_chain(question: str) -> str:
    """Lower-case and rewrite verb/clause forms into ``<noun> of`` form."""
    text = re.sub(r"\s+", " ", question.strip().lower()).rstrip("?.! ")
    for pat, repl in LEAD_REWRITES:
        text = pat.sub(repl, text, count=1)
    for pat, repl in CLAUSE_REWRITES:
        text = pat.sub(repl, text)
    return _EMPLOYER_CLAUSE.sub(r"previous employer of \g<x>", text)


def parse_chain(question: str, entities: list[str]) -> ParsedChain | None:
    """Parse a nested relation chain; ``None`` unless every token is consumed."""
    if not entities:
        return None
    text = _expand_possessives(normalize_chain(question), entities)
    outer: RelationFrame | None = None
    for pat, key in _OUTER:
        m = pat.match(text)
        if m:
            outer, text = frame(key), m.group("inner")
            break
    else:
        text = _LEAD.sub("", text, count=1)
    inner = _parse_noun_chain(text, entities)
    if inner is None:
        return None
    frames = inner.frames + ((outer,) if outer else ())
    if not (_MIN_CHAIN <= len(frames) <= _MAX_CHAIN):
        return None
    return ParsedChain(frames=frames, anchor=inner.anchor)


def _expand_possessives(text: str, entities: list[str]) -> str:
    """``X's parent company`` → ``parent company of X`` for known entities X."""
    for ent in sorted(entities, key=len, reverse=True):
        e = re.escape(ent.lower())
        for noun, _fr in _NOUNS:
            text = re.sub(rf"{e}'s {re.escape(noun)}\b", f"{noun} of {ent.lower()}", text)
    return text


def _parse_noun_chain(text: str, entities: list[str]) -> ParsedChain | None:
    surface: list[RelationFrame] = []
    rest = text
    while True:
        anchor = _anchor_match(rest, entities)
        if anchor is not None:
            return ParsedChain(frames=tuple(reversed(surface)), anchor=anchor)
        step = _consume_frame(rest)
        if step is None:
            return None
        fr, rest = step
        surface.append(fr)


def _consume_frame(text: str) -> tuple[RelationFrame, str] | None:
    for noun, fr in _NOUNS:
        if not text.startswith(noun):
            continue
        m = _OF.match(text[len(noun) :])
        if m:
            return fr, text[len(noun) + m.end() :]
    return None


def _anchor_match(text: str, entities: list[str]) -> str | None:
    t = text.strip()
    for ent in entities:
        if t == ent.lower():
            return ent
    return None


def chain_subquestions(chain: ParsedChain) -> list[SubQuestion]:
    out: list[SubQuestion] = []
    for i, fr in enumerate(chain.frames, start=1):
        if i == 1:
            out.append(
                SubQuestion(
                    id="sq1",
                    text=fr.template.format(x=chain.anchor),
                    rationale=f"chain hop 1: {fr.key} of anchor",
                )
            )
            continue
        prev = f"sq{i - 1}"
        out.append(
            SubQuestion(
                id=f"sq{i}",
                text=fr.template.format(x=f"{{from:{prev}}}"),
                depends_on=[prev],
                rationale=f"chain hop {i}: {fr.key} of resolved {prev}",
                is_placeholder=True,
            )
        )
    return out


def match_relation_chain(ctx: PlanContext) -> list[SubQuestion] | None:
    chain = parse_chain(ctx.q, ctx.entities)
    return chain_subquestions(chain) if chain else None
