"""Conflict detection and resolution for incremental graph updates (FR-KG-05).

Pure decision logic over an in-memory relation index: no store I/O, so the
policy is unit-testable without a backend. ``IncrementalUpdater`` owns the
index, the writes, and the review ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agentic_graphrag.knowledge.schema_check import Triple
from agentic_graphrag.stores.interfaces import RelationRecord

# (head_lower, relation, tail_lower) → active relation
RelIndex = dict[tuple[str, str, str], RelationRecord]

_CONFIDENCE_EPSILON = 1e-9


class ConflictAction(StrEnum):
    AUTO_UPDATE = "auto_update"
    REVIEW = "review"
    KEEP_OLD = "keep_old"


@dataclass
class Conflict:
    relation_key: str  # head|type|tail-type or head|type (value conflict)
    existing: RelationRecord | None
    incoming: Triple
    action: ConflictAction
    reason: str = ""
    # Every existing edge this incoming triple contradicts. ``existing`` is the
    # strongest of them and drives the decision; all of them are retired on
    # AUTO_UPDATE so no dual fact survives (docs/BUSINESS_LOGIC.md BL-11).
    superseded: list[RelationRecord] = field(default_factory=list)

    def edges_to_retire(self) -> list[RelationRecord]:
        if self.superseded:
            return self.superseded
        return [self.existing] if self.existing is not None else []

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_key": self.relation_key,
            "existing": _record_summary(self.existing),
            "superseded": [_record_summary(r) for r in self.edges_to_retire()],
            "incoming": self.incoming.model_dump(mode="json"),
            "action": self.action.value,
            "reason": self.reason,
        }


def _record_summary(record: RelationRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "id": record.id,
        "type": record.type,
        "head": record.head_name,
        "tail": record.tail_name,
        "confidence": record.confidence,
        "valid_from": record.valid_from,
        "valid_to": record.valid_to,
    }


def index_key(head: str, relation: str, tail: str) -> tuple[str, str, str]:
    return ((head or "").lower(), relation, (tail or "").lower())


def triple_key(triple: Triple) -> tuple[str, str, str]:
    return index_key(triple.head.name, triple.relation, triple.tail.name)


def decide_action(old_conf: float, new_conf: float, *, margin: float) -> tuple[ConflictAction, str]:
    if new_conf >= old_conf + margin:
        return ConflictAction.AUTO_UPDATE, "new confidence significantly higher"
    if new_conf > old_conf:
        return ConflictAction.REVIEW, "new confidence only slightly higher"
    return ConflictAction.KEEP_OLD, "existing confidence higher or equal"


def windows_disjoint(a: tuple[str | None, str | None], b: tuple[str | None, str | None]) -> bool:
    """True when two validity windows cannot overlap (ADR-007 / BL-14).

    Each window is ``(valid_from, valid_to)`` over ISO-8601 strings, which
    compare correctly lexicographically across mixed precision (``2024`` <
    ``2024-03`` < ``2024-03-15``). An open end (``None``) reaches to ±infinity.
    """
    a_lo, a_hi = a[0] or "", a[1] or ""
    b_lo, b_hi = b[0] or "", b[1] or ""
    a_before_b = bool(a_hi and b_lo and a_hi < b_lo)
    b_before_a = bool(b_hi and a_lo and b_hi < a_lo)
    return a_before_b or b_before_a


def _temporal_decision(
    incoming: Triple, existing: RelationRecord
) -> tuple[ConflictAction, str] | None:
    """Time-first adjudication when both sides state a validity start (ADR-007).

    Returns ``None`` when time cannot decide (a side lacks ``valid_from`` or
    both start at the same point) so the confidence-margin policy applies.
    """
    new_from = incoming.valid_from or ""
    old_from = existing.valid_from or ""
    if not new_from or not old_from or new_from == old_from:
        return None
    if new_from > old_from:
        return (
            ConflictAction.AUTO_UPDATE,
            f"incoming fact is newer (valid_from {new_from} > {old_from})",
        )
    return (
        ConflictAction.KEEP_OLD,
        f"existing fact is newer (valid_from {old_from} > {new_from})",
    )


def split_by_conflict(
    triples: list[Triple],
    index: RelIndex,
    *,
    margin: float,
) -> tuple[list[Triple], list[Conflict]]:
    """Split into clean inserts vs conflicts against existing edges."""
    clean: list[Triple] = []
    conflicts: list[Conflict] = []
    for triple in triples:
        conflict = _conflict_for(triple, index, margin=margin)
        if conflict is None:
            clean.append(triple)
        else:
            conflicts.append(conflict)
    return clean, conflicts


def _conflict_for(triple: Triple, index: RelIndex, *, margin: float) -> Conflict | None:
    existing = index.get(triple_key(triple))
    if existing is None:
        return _value_conflict(triple, index, margin=margin)
    # Temporal coexistence (ADR-007 / BL-14): the same fact asserted over
    # disjoint periods is two true statements, not a contradiction.
    if windows_disjoint(
        (triple.valid_from, triple.valid_to), (existing.valid_from, existing.valid_to)
    ):
        return None
    decision = _temporal_decision(triple, existing)
    if decision is not None:
        action, reason = decision
    elif triple.confidence <= existing.confidence + _CONFIDENCE_EPSILON:
        # Exact same edge, equal or lower confidence. This must be KEEP_OLD,
        # not a clean insert: a clean insert is written, and the store upserts
        # by id, so "never overwrite" used to silently downgrade the edge.
        action, reason = ConflictAction.KEEP_OLD, "existing confidence higher or equal"
    else:
        action, reason = decide_action(existing.confidence, triple.confidence, margin=margin)
        reason = reason or "higher confidence refresh"
    return Conflict(
        relation_key=f"{triple.head.name}|{triple.relation}|{triple.tail.name}",
        existing=existing,
        incoming=triple,
        action=action,
        reason=reason,
        superseded=[existing],
    )


def _value_conflict(triple: Triple, index: RelIndex, *, margin: float) -> Conflict | None:
    """Same head + relation, different tail — decide over the rivals it contradicts."""
    head = triple.head.name.lower()
    tail = triple.tail.name.lower()
    rivals = [
        rec
        for (h, rel, _), rec in index.items()
        if h == head and rel == triple.relation and (rec.tail_name or "").lower() != tail
    ]
    # Rivals in a disjoint period are not contradicted (ADR-007 / BL-14):
    # "CEO was Alice 2019-2021" and "CEO is Bob 2022-" are both true.
    rivals = [
        rec
        for rec in rivals
        if not windows_disjoint(
            (triple.valid_from, triple.valid_to), (rec.valid_from, rec.valid_to)
        )
    ]
    if not rivals:
        return None
    # Decide against the strongest rival: a new triple must beat the best
    # existing evidence, not merely the first one the index happened to yield.
    strongest = max(rivals, key=lambda r: r.confidence)
    decision = _temporal_decision(triple, strongest)
    if decision is not None:
        action, reason = decision
    else:
        action, reason = decide_action(strongest.confidence, triple.confidence, margin=margin)
    return Conflict(
        relation_key=f"{triple.head.name}|{triple.relation}|*",
        existing=strongest,
        incoming=triple,
        action=action,
        reason=reason,
        superseded=rivals,
    )
