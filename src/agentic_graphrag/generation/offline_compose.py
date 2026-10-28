"""Structured offline answer composers (constraint intersection, shared connections).

Both compose the answer from relation semantics over direction-aware edges
instead of dumping explored paths, so the answer names exactly what was asked.
"""

from __future__ import annotations

import re
from collections import defaultdict

Edge = tuple[str, str, str]

_SHARED = re.compile(
    r"\b(?:shared|common|mutual) (?:business )?(?:connections?|links?|ties|relationships?) "
    r"(?:between|of) (?P<a>.+?) and (?P<b>.+?)[?.!]*$",
    re.IGNORECASE,
)
_IN_COMMON = re.compile(r"^what do (?P<a>.+?) and (?P<b>.+?) have in common[?.!]*$", re.IGNORECASE)
# Direct A–B edge → clause (A/B substituted).
_DIRECT: dict[str, str] = {
    "COMPETES_WITH": "they compete",
    "PARTNER_OF": "they are partners",
    "SUPPLIES": "{h} supplies {t}",
    "SUPPLIES_FOR": "{h} supplies {t}",
    "PARENT_OF": "{h} is the parent company of {t}",
    "SUBSIDIARY_OF": "{h} is a subsidiary of {t}",
    "ACQUIRED": "{h} acquired {t}",
}
# Shared neighbor N, keyed by (relation, N is head): phrase before the names.
_COMMON: dict[tuple[str, bool], str] = {
    ("SUPPLIES", True): "share suppliers",
    ("SUPPLIES_FOR", True): "share suppliers",
    ("SUPPLIES", False): "share customers",
    ("PARENT_OF", True): "share parent company",
    ("SUBSIDIARY_OF", False): "share parent company",
    ("CEO_OF", True): "share CEO",
    ("WORKED_AT", True): "share former employees",
    ("PARTICIPATED_IN", False): "both participated in",
    ("LOCATED_IN", False): "are both located in",
    ("COMPETES_WITH", True): "share competitors",
    ("COMPETES_WITH", False): "share competitors",
    ("PARTNER_OF", True): "share partners",
    ("PARTNER_OF", False): "share partners",
    ("ACQUIRED", True): "were both acquired by",
    ("PRODUCES", False): "both produce",
}


def shared_pair(question: str, entities: list[str]) -> tuple[str, str] | None:
    """The two known entities a "shared connections between A and B" question names."""
    q = re.sub(r"\s+", " ", (question or "").strip())
    m = _SHARED.search(q) or _IN_COMMON.match(q)
    if m is None:
        return None
    a, b = _canon(m.group("a"), entities), _canon(m.group("b"), entities)
    return (a, b) if a and b and a.lower() != b.lower() else None


def _canon(text: str, entities: list[str]) -> str | None:
    t = re.sub(r"^the ", "", text.strip().lower())
    return next((e for e in entities if e.lower() == t), None)


def compose_shared_connections(pair: tuple[str, str], edges: list[Edge]) -> str | None:
    """ "They compete; share suppliers X and Y" from edges around A and B."""
    a, b = (p.lower() for p in pair)
    direct: list[str] = []
    roles: dict[tuple[str, bool], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for edge in dict.fromkeys(edges):
        clause = _classify(edge, (a, b), roles)
        if clause and clause not in direct:
            direct.append(clause)
    common = [_common_clause(key, nbrs) for key, nbrs in roles.items()]
    parts = direct + [c for c in common if c]
    return "; ".join(parts)[:1].upper() + "; ".join(parts)[1:] if parts else None


def _classify(
    edge: Edge,
    pair: tuple[str, str],
    roles: dict[tuple[str, bool], dict[str, set[str]]],
) -> str | None:
    """Direct A–B clause, or record the edge's outside endpoint as a shared-neighbor role."""
    head, rel, tail = edge
    h, t = head.lower(), tail.lower()
    if {h, t} == set(pair):
        return _DIRECT.get(rel, f"{{h}} {rel.lower()} {{t}}").format(h=head, t=tail)
    if t in pair:
        roles[(rel, True)][head].add(t)
    elif h in pair:
        roles[(rel, False)][tail].add(h)
    return None


def _common_clause(key: tuple[str, bool], nbrs: dict[str, set[str]]) -> str | None:
    shared = sorted(n for n, ends in nbrs.items() if len(ends) == 2)
    if not shared:
        return None
    rel, _ = key
    phrase = _COMMON.get(key, f"are both linked via {rel.lower()} to")
    return f"{phrase} {' and '.join(shared)}"


def constraint_answer(question: str, conclusions: str, entities: list[str]) -> str | None:
    """Intersection of every clause's final conclusion for constraint questions."""
    from agentic_graphrag.agent.planner_compound import compound_answer
    from agentic_graphrag.agent.planner_constraints import intersect_conclusions

    parts = [p.strip() for p in (conclusions or "").split(";") if p.strip()]
    if not parts:
        return None
    return compound_answer(question, parts, entities) or intersect_conclusions(
        question, parts, entities
    )
