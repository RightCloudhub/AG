"""Relation cardinality: which sides of a relation are single-valued.

A *value conflict* only exists for single-valued relations: a company has one
CEO, one headquarters, one direct parent. Multi-valued relations (a company
``PRODUCES`` many products, a person ``WORKED_AT`` many employers) must never
retire a sibling edge just because a new tail arrived. The schema declares this
per relation with ``functional_on: [head]`` / ``[tail]`` / ``[head, tail]`` /
``[]``; relations without the key keep the historical behaviour (``[head]``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

HEAD = "head"
TAIL = "tail"
LEGACY_SIDES: frozenset[str] = frozenset({HEAD})

SidesFn = Callable[[str], frozenset[str]]


def functional_sides(relation_meta: dict[str, Any] | None) -> frozenset[str]:
    """Sides on which ``relation_meta`` is single-valued."""
    if not relation_meta or "functional_on" not in relation_meta:
        return LEGACY_SIDES
    raw = relation_meta.get("functional_on") or []
    if isinstance(raw, str):
        raw = [raw]
    return frozenset(str(side) for side in raw if side in (HEAD, TAIL))


def sides_resolver(schema: Any | None) -> SidesFn:
    """Build a ``relation → sides`` lookup from a ``SchemaDefinition`` (or None)."""
    relation_types: dict[str, dict[str, Any]] = getattr(schema, "relation_types", None) or {}

    def resolve(relation: str) -> frozenset[str]:
        if relation not in relation_types:
            return LEGACY_SIDES
        return functional_sides(relation_types[relation])

    return resolve


def legacy_sides(_relation: str) -> frozenset[str]:
    return LEGACY_SIDES
