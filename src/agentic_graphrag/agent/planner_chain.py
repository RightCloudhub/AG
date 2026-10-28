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
    (
        re.compile(r"^which (?:companies|company|firms?) did (?:the )?(?P<inner>.+) acquire$"),
        "acquisition",
    ),
)
# "<qualifier> <noun> of": up to three lower-case words naming *what* is supplied.
_QUALIFIER = re.compile(r"^(?P<q>[a-z][a-z\-]*(?: [a-z][a-z\-]*){0,2}) ")
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
    # Per-frame qualifier ("chip" in "chip supplier of X"), aligned with ``frames``.
    qualifiers: tuple[str, ...] = ()

    def qualifier(self, i: int) -> str:
        return self.qualifiers[i] if i < len(self.qualifiers) else ""


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
    return _parse(question, entities, min_frames=_MIN_CHAIN)


def parse_any_chain(question: str, entities: list[str]) -> ParsedChain | None:
    """Like :func:`parse_chain` but a single frame counts (compound follow-ups)."""
    return _parse(question, entities, min_frames=1)


def _parse(question: str, entities: list[str], *, min_frames: int) -> ParsedChain | None:
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
    inner = _parse_noun_chain(text, entities) if not _anchor_match(text, entities) else None
    if inner is None and outer is not None and _anchor_match(text, entities):
        inner = ParsedChain(frames=(), anchor=_anchor_match(text, entities) or "")
    if inner is None:
        return None
    frames = inner.frames + ((outer,) if outer else ())
    if not (min_frames <= len(frames) <= _MAX_CHAIN):
        return None
    quals = inner.qualifiers + (("",) if outer else ())
    return ParsedChain(frames=frames, anchor=inner.anchor, qualifiers=quals)


def _expand_possessives(text: str, entities: list[str]) -> str:
    """``X's parent company`` → ``parent company of X`` for known entities X."""
    if "'s " not in text:
        return text
    for ent in sorted(entities, key=len, reverse=True):
        e = re.escape(ent.lower())
        for noun, _fr in _NOUNS:
            text = re.sub(rf"{e}'s {re.escape(noun)}\b", f"{noun} of {ent.lower()}", text)
    return text


def parse_noun_phrase(text: str, entities: list[str]) -> ParsedChain | None:
    """Parse "the chip supplier of X" (one or more frames) — for clause objects."""
    t = _expand_possessives(normalize_chain(text), entities)
    parsed = _parse_noun_chain(re.sub(r"^the ", "", t), entities)
    return parsed if parsed and parsed.frames else None


def _parse_noun_chain(text: str, entities: list[str]) -> ParsedChain | None:
    surface: list[tuple[RelationFrame, str]] = []
    rest = text
    while True:
        anchor = _anchor_match(rest, entities)
        if anchor is not None:
            ordered = tuple(reversed(surface))
            return ParsedChain(
                frames=tuple(f for f, _ in ordered),
                anchor=anchor,
                qualifiers=tuple(q for _, q in ordered),
            )
        step = _consume_frame(rest, entities)
        if step is None:
            return None
        fr, qual, rest = step
        surface.append((fr, qual))


def _consume_frame(text: str, entities: list[str]) -> tuple[RelationFrame, str, str] | None:
    plain = _consume_noun(text)
    if plain is not None:
        return plain[0], "", plain[1]
    m = _QUALIFIER.match(text)
    if m is None:
        return None
    qual = m.group("q")
    rest = text[m.end() :]
    for cut in range(len(qual.split()), 0, -1):
        # Shortest remainder first: "advanced chip supplier of" → qualifier "advanced chip".
        words = qual.split()[:cut]
        tail = " ".join(qual.split()[cut:])
        cand = f"{tail} {rest}".strip()
        hit = _consume_noun(cand)
        q = " ".join(words)
        if hit and hit[0].qualified and not _names_entity(q, entities):
            return hit[0], q, hit[1]
    return None


def _consume_noun(text: str) -> tuple[RelationFrame, str] | None:
    for noun, fr in _NOUNS:
        if not text.startswith(noun):
            continue
        m = _OF.match(text[len(noun) :])
        if m:
            return fr, text[len(noun) + m.end() :]
    return None


def _names_entity(text: str, entities: list[str]) -> bool:
    return any(e.lower() in text or text in e.lower() for e in entities)


def _anchor_match(text: str, entities: list[str]) -> str | None:
    t = text.strip()
    for ent in entities:
        if t == ent.lower():
            return ent
    return None


def chain_subquestions(chain: ParsedChain) -> list[SubQuestion]:
    return chain_subquestions_from(chain, start=1)


def chain_subquestions_from(
    chain: ParsedChain, *, start: int, after: str | None = None
) -> list[SubQuestion]:
    """Sub-questions ``sq{start}..`` for ``chain``, each hop feeding the next.

    With ``after`` the first hop is applied to that earlier step's conclusion
    instead of the anchor (compound "..., and who leads it?").
    """
    out: list[SubQuestion] = []
    for i, fr in enumerate(chain.frames):
        n, qual = start + i, chain.qualifier(i)
        tmpl = fr.qualified if qual and fr.qualified else fr.template
        if i == 0:
            x = f"{{from:{after}}}" if after else chain.anchor
            out.append(
                SubQuestion(
                    id=f"sq{n}",
                    text=tmpl.format(x=x, q=qual),
                    depends_on=[after] if after else [],
                    rationale=f"chain hop 1: {fr.key} of {after or 'anchor'}",
                    is_placeholder=bool(after),
                )
            )
            continue
        prev = f"sq{n - 1}"
        out.append(
            SubQuestion(
                id=f"sq{n}",
                text=tmpl.format(x=f"{{from:{prev}}}", q=qual),
                depends_on=[prev],
                rationale=f"chain hop {i + 1}: {fr.key} of resolved {prev}",
                is_placeholder=True,
            )
        )
    return out


def match_relation_chain(ctx: PlanContext) -> list[SubQuestion] | None:
    chain = parse_chain(ctx.q, ctx.entities)
    return chain_subquestions(chain) if chain else None
