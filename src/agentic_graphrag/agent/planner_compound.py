"""Compound questions: "<question about X>, and who leads it?".

The first part is planned as a relation chain (or constraint set); the
follow-up refers back with a pronoun, so its chain starts from the first part's
final conclusion. The answer reports both parts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentic_graphrag.agent.plan_dag import SubQuestion
from agentic_graphrag.agent.planner_chain import (
    ParsedChain,
    chain_subquestions_from,
    parse_any_chain,
)
from agentic_graphrag.agent.planner_constraints import constraint_subquestions, parse_constraints

if TYPE_CHECKING:
    from agentic_graphrag.agent.planner_patterns import PlanContext

_SPLIT = re.compile(
    r"^(?P<q1>.+?),? and (?P<q2>(?:who|what|where|which)\b.*\b(?:it|its|they|them|their)\b.*)$",
    re.IGNORECASE,
)
_REF = "refentity"
_PRONOUN = re.compile(r"\b(?:it|they|them)\b")
_POSSESSIVE = re.compile(r"\b(?:its|their) ")


@dataclass(frozen=True)
class Compound:
    first: list[SubQuestion]
    follow: ParsedChain


def parse_compound(question: str, entities: list[str]) -> Compound | None:
    text = re.sub(r"\s+", " ", (question or "").strip()).rstrip("?.! ")
    m = _SPLIT.match(text)
    if m is None or not entities:
        return None
    first = _first_part(m.group("q1"), entities)
    if not first:
        return None
    q2 = _POSSESSIVE.sub(f"{_REF}'s ", m.group("q2").lower())
    follow = parse_any_chain(_PRONOUN.sub(_REF, q2), [_REF])
    return Compound(first=first, follow=follow) if follow else None


def _first_part(q1: str, entities: list[str]) -> list[SubQuestion] | None:
    chain = parse_any_chain(q1, entities)
    if chain is not None:
        return chain_subquestions_from(chain, start=1)
    constraints = parse_constraints(q1, entities)
    return constraint_subquestions(constraints) if constraints else None


def compound_subquestions(c: Compound) -> list[SubQuestion]:
    last = c.first[-1].id
    return [*c.first, *chain_subquestions_from(c.follow, start=len(c.first) + 1, after=last)]


def match_compound(ctx: PlanContext) -> list[SubQuestion] | None:
    compound = parse_compound(ctx.q, ctx.entities)
    return compound_subquestions(compound) if compound else None


def compound_answer(question: str, conclusions: list[str], entities: list[str]) -> str | None:
    """ "<first answer>; <follow-up answer>" once every planned hop concluded."""
    c = parse_compound(question, entities)
    if c is None or len(conclusions) != len(c.first) + len(c.follow.frames):
        return None
    return f"{conclusions[len(c.first) - 1]}; {conclusions[-1]}"
