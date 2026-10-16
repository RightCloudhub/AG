"""Conflict detection and resolution for incremental graph updates (FR-KG-05).

Pure decision logic over an in-memory relation index: no store I/O, so the
policy is unit-testable without a backend. ``IncrementalUpdater`` owns the
index, the writes, and the review ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agentic_graphrag.knowledge.cardinality import HEAD, TAIL, SidesFn, legacy_sides
from agentic_graphrag.knowledge.schema_check import Triple
from agentic_graphrag.knowledge.temporal import VALID_FROM, closed, is_newer
from agentic_graphrag.stores.interfaces import RelationRecord

# (head_lower, relation, tail_lower) → active relation
RelIndex = dict[tuple[str, str, str], RelationRecord]

_CONFIDENCE_EPSILON = 1e-9


class ConflictAction(StrEnum):
    AUTO_UPDATE = "auto_update"
    REVIEW = "review"
    KEEP_OLD = "keep_old"
    # BL-14: dated facts are decided by time. The newer fact becomes current and
    # the older one is closed (``valid_to``) instead of deleted.
    SUCCEED = "succeed"
    HISTORICAL = "historical"


@dataclass
class Conflict:
    relation_key: str  # head|type|tail-type or head|type (value conflict)
    existing: RelationRecord | None
    incoming: Triple
    action: ConflictAction
    reason: str = ""
    # Every existing edge this incoming triple contradicts. ``existing`` is the
    # strongest of them and drives the decision; all of them are retired on
    # AUTO_UPDATE so no dual fact survives (docs/design/BUSINESS_LOGIC.md BL-11).
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
    sides_for: SidesFn = legacy_sides,
) -> tuple[list[Triple], list[Conflict]]:
    """Split into clean inserts vs conflicts against existing edges."""
    clean: list[Triple] = []
    conflicts: list[Conflict] = []
    for triple in triples:
        conflict = _conflict_for(triple, index, margin=margin, sides_for=sides_for)
        if conflict is None:
            clean.append(triple)
        else:
            conflicts.append(conflict)
    return clean, conflicts


def _conflict_for(
    triple: Triple, index: RelIndex, *, margin: float, sides_for: SidesFn
) -> Conflict | None:
    existing = index.get(triple_key(triple))
    if existing is None:
        return _value_conflict(triple, index, margin=margin, sides=sides_for(triple.relation))
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


def find_rivals(triple: Triple, index: RelIndex, sides: frozenset[str]) -> list[RelationRecord]:
    """Existing edges that contradict ``triple`` on a single-valued side."""
    head = triple.head.name.lower()
    tail = triple.tail.name.lower()
    rivals: list[RelationRecord] = []
    for (h, rel, t), rec in index.items():
        if rel != triple.relation or (h == head and t == tail):
            continue
        same_head = HEAD in sides and h == head
        same_tail = TAIL in sides and t == tail
        if same_head or same_tail:
            rivals.append(rec)
    return rivals


def _value_conflict(
    triple: Triple, index: RelIndex, *, margin: float, sides: frozenset[str]
) -> Conflict | None:
    """Single-valued relation got a different value — one decision over *all* rivals."""
    rivals = find_rivals(triple, index, sides) if sides else []
    if not rivals:
        return None
    temporal = _temporal_conflict(triple, rivals)
    if temporal is not None:
        return temporal
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


def _temporal_conflict(triple: Triple, rivals: list[RelationRecord]) -> Conflict | None:
    """Decide by dates when every party is dated (BL-14); else ``None``."""
    verdicts = [is_newer(triple.attributes, rec.attributes) for rec in rivals]
    if not verdicts or any(v is None for v in verdicts):
        return None
    latest = max(rivals, key=lambda r: str(r.attributes.get(VALID_FROM)))
    key = f"{triple.head.name}|{triple.relation}|*"
    if all(verdicts):
        return Conflict(
            relation_key=key,
            existing=latest,
            incoming=triple,
            action=ConflictAction.SUCCEED,
            reason="incoming fact starts later; older facts closed",
            superseded=rivals,
        )
    first_later = min(
        (r for r, newer in zip(rivals, verdicts, strict=True) if not newer),
        key=lambda r: str(r.attributes.get(VALID_FROM)),
    )
    historic = triple.model_copy(deep=True)
    historic.attributes = closed(triple.attributes, first_later.attributes.get(VALID_FROM))
    return Conflict(
        relation_key=key,
        existing=first_later,
        incoming=historic,
        action=ConflictAction.HISTORICAL,
        reason="incoming fact predates the current one; stored as history",
        # Rivals that started even earlier still end where the incoming fact begins.
        superseded=[r for r, newer in zip(rivals, verdicts, strict=True) if newer],
    )
