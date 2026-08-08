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
    # Exact same edge — only upgrade when confidence is meaningfully higher.
    if triple.confidence <= existing.confidence + _CONFIDENCE_EPSILON:
        return None  # equal or lower confidence: never overwrite
    action, reason = decide_action(existing.confidence, triple.confidence, margin=margin)
    return Conflict(
        relation_key=f"{triple.head.name}|{triple.relation}|{triple.tail.name}",
        existing=existing,
        incoming=triple,
        action=action,
        reason=reason or "higher confidence refresh",
        superseded=[existing],
    )


def _value_conflict(triple: Triple, index: RelIndex, *, margin: float) -> Conflict | None:
    """Same head + relation, different tail — one decision over *all* old edges."""
    head = triple.head.name.lower()
    tail = triple.tail.name.lower()
    rivals = [
        rec
        for (h, rel, _), rec in index.items()
        if h == head and rel == triple.relation and (rec.tail_name or "").lower() != tail
    ]
    if not rivals:
        return None
    # Decide against the strongest rival: a new triple must beat the best
    # existing evidence, not merely the first one the index happened to yield.
    strongest = max(rivals, key=lambda r: r.confidence)
    action, reason = decide_action(strongest.confidence, triple.confidence, margin=margin)
    return Conflict(
        relation_key=f"{triple.head.name}|{triple.relation}|*",
        existing=strongest,
        incoming=triple,
        action=action,
        reason=reason,
        superseded=rivals,
    )
