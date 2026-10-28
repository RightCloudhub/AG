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
    # Optional "what" qualifier form ("chip supplier of X" → "supply chips for X").
    qualified: str = ""


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
        qualified="Which companies supply {q} for {x}?",
    ),
    RelationFrame(
        "acquisition",
        ("acquisitions", "acquisition", "acquired company"),
        (("ACQUIRED", Side.TAIL),),
        "Which company did {x} acquire?",
        multi=True,
    ),
    RelationFrame(
        "acquirer",
        ("acquirer", "buyer"),
        (("ACQUIRED", Side.HEAD),),
        "Which company acquired {x}?",
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
        "alumni",
        ("former employees", "alumni"),
        (("WORKED_AT", Side.HEAD),),
        "Who previously worked at {x}?",
        multi=True,
    ),
    RelationFrame(
        "participant",
        ("participants",),
        (("PARTICIPATED_IN", Side.HEAD),),
        "Which companies participated in {x}?",
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
    # "company that supplies power modules for X" keeps its qualifier.
    (
        re.compile(
            r"\b(?:company|firm|business) (?:that|which) supplies "
            r"(?P<q>(?!for\b|to\b)[a-z][a-z\-]*(?: [a-z][a-z\-]*){0,2}) (?:for|to)\b"
        ),
        r"\g<q> supplier of",
    ),
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
    (re.compile(r"^who (?:runs|leads|heads|manages) (?:the )?"), "who is the ceo of the "),
    (
        re.compile(r"^which (?:company|companies|firm) (?:owns|own|controls) (?:the )?"),
        "what is the parent company of the ",
    ),
    (
        re.compile(r"^which (?:company|companies|firm) (?:produces|makes) (?:the )?"),
        "what is the producer of the ",
    ),
    (
        re.compile(r"^which (?:company|companies|firm) (?:acquired|bought) (?:the )?"),
        "what is the acquirer of the ",
    ),
)


def frame(key: str) -> RelationFrame:
    return _BY_KEY[key]
