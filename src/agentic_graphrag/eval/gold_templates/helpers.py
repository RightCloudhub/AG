"""Pure graph helpers shared by gold path templates."""

from __future__ import annotations

from agentic_graphrag.eval.gold_index import Edge

# Relation name constants (no bare magic strings in templates).
REL_SUBSIDIARY_OF = "SUBSIDIARY_OF"
REL_PARENT_OF = "PARENT_OF"
REL_CEO_OF = "CEO_OF"
REL_WORKED_AT = "WORKED_AT"
REL_PRODUCES = "PRODUCES"
REL_COMPETES_WITH = "COMPETES_WITH"
REL_SUPPLIES_FOR = "SUPPLIES_FOR"
REL_SUPPLIES = "SUPPLIES"
REL_PARTICIPATED_IN = "PARTICIPATED_IN"

PARENT_RELATIONS = frozenset({REL_SUBSIDIARY_OF, REL_PARENT_OF})
OPEN_COMPANY_HEAD_RELS = frozenset({REL_COMPETES_WITH, REL_PARENT_OF})
OPEN_COMPANY_TAIL_RELS = frozenset({REL_COMPETES_WITH, REL_SUBSIDIARY_OF})

NO_ANSWER_TEXT = "no answer"
MIN_EVENT_PARTICIPANTS = 2


def join_names(names: list[str]) -> str:
    """Join unique names in first-seen order with ' and '."""
    return " and ".join(dict.fromkeys(names))


def child_parent_of(edge: Edge) -> tuple[str, str] | None:
    """Return (child, parent) for subsidiary/parent edges, else None."""
    if edge.relation == REL_SUBSIDIARY_OF:
        return edge.head, edge.tail
    if edge.relation == REL_PARENT_OF:
        return edge.tail, edge.head
    return None


def parents_of(
    node: str,
    out_adj: dict[str, list[Edge]],
    in_adj: dict[str, list[Edge]],
) -> list[str]:
    """Parent companies of node via SUBSIDIARY_OF / PARENT_OF."""
    parents: list[str] = []
    for x in out_adj.get(node, []) + in_adj.get(node, []):
        if x.relation == REL_SUBSIDIARY_OF and x.head == node:
            parents.append(x.tail)
        elif x.relation == REL_PARENT_OF and x.tail == node:
            parents.append(x.head)
    return parents


def competitors_of(
    node: str,
    out_adj: dict[str, list[Edge]],
    in_adj: dict[str, list[Edge]],
) -> list[str]:
    """Other side of COMPETES_WITH edges incident on node."""
    comps: list[str] = []
    for x in out_adj.get(node, []) + in_adj.get(node, []):
        if x.relation != REL_COMPETES_WITH:
            continue
        comps.append(x.tail if x.head == node else x.head)
    return comps


def ceos_of(node: str, in_adj: dict[str, list[Edge]]) -> list[str]:
    return [x.head for x in in_adj.get(node, []) if x.relation == REL_CEO_OF]


def employers_of(person: str, out_adj: dict[str, list[Edge]]) -> list[str]:
    return [x.tail for x in out_adj.get(person, []) if x.relation == REL_WORKED_AT]


def suppliers_of(company: str, in_adj: dict[str, list[Edge]]) -> list[str]:
    return [x.head for x in in_adj.get(company, []) if x.relation == REL_SUPPLIES]


def grandparents_of(
    parent: str,
    out_adj: dict[str, list[Edge]],
    in_adj: dict[str, list[Edge]],
) -> list[str]:
    """One hop up from parent via subsidiary/parent edges."""
    gps = [x.tail for x in out_adj.get(parent, []) if x.relation == REL_SUBSIDIARY_OF]
    gps += [
        x.head for x in in_adj.get(parent, []) if x.relation == REL_PARENT_OF and x.tail == parent
    ]
    return gps


def producers_of_product(product: str, edges: list[Edge]) -> list[str]:
    return [x.head for x in edges if x.relation == REL_PRODUCES and x.tail == product]


def company_entities(edges: list[Edge]) -> list[str]:
    """Sorted companies appearing on competes/parent/subsidiary edges."""
    heads = {e.head for e in edges if e.relation in OPEN_COMPANY_HEAD_RELS}
    tails = {e.tail for e in edges if e.relation in OPEN_COMPANY_TAIL_RELS}
    return sorted(heads | tails)


def competes_linked(edges: list[Edge], a: str, b: str) -> bool:
    for x in edges:
        if x.relation != REL_COMPETES_WITH:
            continue
        if (x.head == a and x.tail == b) or (x.head == b and x.tail == a):
            return True
    return False


def event_participants(edges: list[Edge]) -> dict[str, list[str]]:
    events: dict[str, list[str]] = {}
    for e in edges:
        if e.relation != REL_PARTICIPATED_IN:
            continue
        events.setdefault(e.tail, []).append(e.head)
    return events


def known_entities(edges: list[Edge]) -> set[str]:
    return {e.head for e in edges} | {e.tail for e in edges}
