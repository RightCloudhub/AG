"""Constraint-intersection planning: "Which CEO previously worked at X and now leads Y?"

The answer is the set of entities satisfying every clause. Each clause is an
*inverse* frame (the clause's object is the anchor), planned as an independent
sub-question; the answer intersects the clauses' final conclusions. A clause
object may itself be a relation chain ("was acquired by the chip supplier of X"),
in which case the chain is planned first and feeds the clause via ``{from:sqN}``.

A single clause is planned too ("Which executives previously worked at X?"),
because its canonical sub-question gets a direction-checked frame answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentic_graphrag.agent.plan_dag import SubQuestion
from agentic_graphrag.agent.planner_chain import (
    ParsedChain,
    chain_subquestions_from,
    parse_noun_phrase,
)
from agentic_graphrag.agent.relation_frames import RelationFrame, frame

if TYPE_CHECKING:
    from agentic_graphrag.agent.planner_patterns import PlanContext

_HEAD = re.compile(r"^(?:which|who|what)(?: (?:\w+))? (?P<body>.+)$")
_FILLER = re.compile(r"^(?:in (?:this|the) (?:corpus|dataset|graph|data|knowledge base) )")
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
    (re.compile(r"^(?:was |were |has been )?(?:acquired|bought) by (?P<x>.+)$"), "acquisition"),
    (re.compile(r"^(?:acquired|bought) (?P<x>.+)$"), "acquirer"),
)


@dataclass(frozen=True)
class Constraint:
    frame: RelationFrame
    anchor: str
    chain: ParsedChain | None = None

    @property
    def n_steps(self) -> int:
        return 1 + (len(self.chain.frames) if self.chain else 0)


def parse_constraints(question: str, entities: list[str]) -> list[Constraint] | None:
    text = re.sub(r"\s+", " ", question.strip().lower()).rstrip("?.! ")
    m = _HEAD.match(text)
    if m is None or not entities:
        return None
    body = _FILLER.sub("", m.group("body"))
    clauses = [_ADVERBS.sub("", c.strip()) for c in _SPLIT.split(body)]
    parsed = [_parse_clause(c, entities) for c in clauses]
    if not parsed or any(p is None for p in parsed):
        return None
    return [p for p in parsed if p is not None]


def _parse_clause(clause: str, entities: list[str]) -> Constraint | None:
    for pat, key in _CLAUSES:
        m = pat.match(clause)
        if m is None:
            continue
        obj = re.sub(r"^the ", "", m.group("x").strip())
        anchor = next((e for e in entities if e.lower() == obj), None)
        if anchor:
            return Constraint(frame=frame(key), anchor=anchor)
        chain = parse_noun_phrase(obj, entities)
        return Constraint(frame=frame(key), anchor=chain.anchor, chain=chain) if chain else None
    return None


def constraint_subquestions(constraints: list[Constraint]) -> list[SubQuestion]:
    out: list[SubQuestion] = []
    for i, c in enumerate(constraints, start=1):
        n = len(out) + 1
        if c.chain is None:
            out.append(
                SubQuestion(
                    id=f"sq{n}",
                    text=c.frame.template.format(x=c.anchor),
                    rationale=f"constraint {i}: {c.frame.key} of {c.anchor} (intersect)",
                )
            )
            continue
        out.extend(chain_subquestions_from(c.chain, start=n))
        prev = f"sq{len(out)}"
        out.append(
            SubQuestion(
                id=f"sq{len(out) + 1}",
                text=c.frame.template.format(x=f"{{from:{prev}}}"),
                depends_on=[prev],
                rationale=f"constraint {i}: {c.frame.key} of resolved {prev} (intersect)",
                is_placeholder=True,
            )
        )
    return out


def match_constraint_intersection(ctx: PlanContext) -> list[SubQuestion] | None:
    constraints = parse_constraints(ctx.q, ctx.entities)
    return constraint_subquestions(constraints) if constraints else None


def intersect_conclusions(question: str, conclusions: list[str], entities: list[str]) -> str | None:
    """Answer = entities present in every clause's final conclusion (case-insensitive)."""
    constraints = parse_constraints(question, entities)
    if constraints is None or len(conclusions) != sum(c.n_steps for c in constraints):
        return None
    finals, pos = [], 0
    for c in constraints:
        pos += c.n_steps
        finals.append(_names(conclusions[pos - 1]))
    common = [n for n in finals[0] if all(n.lower() in {x.lower() for x in s} for s in finals[1:])]
    return " and ".join(common) if common else None


def _names(conclusion: str) -> list[str]:
    return [p.strip() for p in conclusion.split(" and ") if p.strip()]
