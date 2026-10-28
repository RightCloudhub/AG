"""Constraint-intersection planning: "Which CEO previously worked at X and now leads Y?"

The answer is the set of entities satisfying every clause. Each clause is an
*inverse* frame (the anchor is the clause's object), planned as an independent
sub-question; the answer step intersects their conclusions. Only questions whose
every clause parses are planned here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentic_graphrag.agent.plan_dag import SubQuestion
from agentic_graphrag.agent.relation_frames import RelationFrame, frame

if TYPE_CHECKING:
    from agentic_graphrag.agent.planner_patterns import PlanContext

_MIN_CLAUSES = 2
_HEAD = re.compile(r"^(?:which|who|what)(?: (?:\w+))? (?P<body>.+)$")
_SPLIT = re.compile(r",? and (?:also )?")
_ADVERBS = re.compile(r"^(?:now|also|currently|both) ")
# Clause verb phrase (object = anchor) → frame whose template asks for the subject.
_CLAUSES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^(?:previously |formerly )?(?:worked|works|work) (?:at|for) (?P<x>.+)$"),
        "alumni",
    ),
    (re.compile(r"^(?:leads|runs|heads|is the ceo of|is ceo of) (?P<x>.+)$"), "ceo"),
    (re.compile(r"^(?:produces|makes|manufactures) (?P<x>.+)$"), "producer"),
    (re.compile(r"^(?:competes|compete) with (?P<x>.+)$"), "competitor"),
    (re.compile(r"^(?:supplies|supply) (?P<x>.+)$"), "supplier"),
    (re.compile(r"^(?:owns|own|controls) (?P<x>.+)$"), "parent"),
    (re.compile(r"^(?:participated|participates|took part) in (?P<x>.+)$"), "participant"),
)


@dataclass(frozen=True)
class Constraint:
    frame: RelationFrame
    anchor: str


def parse_constraints(question: str, entities: list[str]) -> list[Constraint] | None:
    text = re.sub(r"\s+", " ", question.strip().lower()).rstrip("?.! ")
    m = _HEAD.match(text)
    if m is None or not entities:
        return None
    clauses = [_ADVERBS.sub("", c.strip()) for c in _SPLIT.split(m.group("body"))]
    if len(clauses) < _MIN_CLAUSES:
        return None
    parsed = [_parse_clause(c, entities) for c in clauses]
    if any(p is None for p in parsed):
        return None
    return [p for p in parsed if p is not None]


def _parse_clause(clause: str, entities: list[str]) -> Constraint | None:
    for pat, key in _CLAUSES:
        m = pat.match(clause)
        if m is None:
            continue
        anchor = next((e for e in entities if e.lower() == m.group("x").strip()), None)
        return Constraint(frame=frame(key), anchor=anchor) if anchor else None
    return None


def match_constraint_intersection(ctx: PlanContext) -> list[SubQuestion] | None:
    constraints = parse_constraints(ctx.q, ctx.entities)
    if constraints is None:
        return None
    return [
        SubQuestion(
            id=f"sq{i}",
            text=c.frame.template.format(x=c.anchor),
            rationale=f"constraint {i}: {c.frame.key} of {c.anchor} (intersect)",
        )
        for i, c in enumerate(constraints, start=1)
    ]


def intersect_conclusions(question: str, conclusions: list[str], entities: list[str]) -> str | None:
    """Answer = entities present in every clause's conclusion (case-insensitive)."""
    constraints = parse_constraints(question, entities)
    if constraints is None or len(conclusions) < len(constraints):
        return None
    sets = [_names(c) for c in conclusions[-len(constraints) :]]
    common = [n for n in sets[0] if all(n.lower() in {x.lower() for x in s} for s in sets[1:])]
    return " and ".join(common) if common else None


def _names(conclusion: str) -> list[str]:
    return [p.strip() for p in conclusion.split(" and ") if p.strip()]
