"""Incremental graph update with conflict detection (FR-KG-05 / P3-KG-01).

New docs → extract → conflict detect → high-conf auto-merge / low-conf review
→ index sync. Online queries keep reading while batches commit.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph, triples_to_records
from agentic_graphrag.knowledge.schema_check import SchemaDefinition, Triple, gate_triples
from agentic_graphrag.stores.interfaces import GraphStore, RelationRecord


class ConflictAction(StrEnum):
    AUTO_UPDATE = "auto_update"
    REVIEW = "review"
    KEEP_OLD = "keep_old"
    SKIP = "skip"


@dataclass
class Conflict:
    relation_key: str  # head|type|tail-type or head|type (value conflict)
    existing: RelationRecord | None
    incoming: Triple
    action: ConflictAction
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_key": self.relation_key,
            "existing": {
                "id": self.existing.id,
                "type": self.existing.type,
                "head": self.existing.head_name,
                "tail": self.existing.tail_name,
                "confidence": self.existing.confidence,
            }
            if self.existing
            else None,
            "incoming": self.incoming.model_dump(mode="json"),
            "action": self.action.value,
            "reason": self.reason,
        }


@dataclass
class BatchResult:
    batch_id: str
    accepted: int = 0
    rejected: int = 0
    conflicts_auto: int = 0
    conflicts_review: int = 0
    review_items: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    index_version: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "conflicts_auto": self.conflicts_auto,
            "conflicts_review": self.conflicts_review,
            "review_items": self.review_items,
            "duration_ms": self.duration_ms,
            "index_version": self.index_version,
        }


class IncrementalUpdater:
    """Apply triple batches without full graph clear (AC-5)."""

    def __init__(
        self,
        store: GraphStore,
        *,
        schema: SchemaDefinition | None = None,
        confidence_threshold: float = 0.5,
        auto_update_margin: float = 0.15,
        on_commit: Callable[[], None] | None = None,
        review_log: Path | str | None = None,
    ) -> None:
        self.store = store
        self.schema = schema
        self.confidence_threshold = confidence_threshold
        self.auto_update_margin = auto_update_margin
        self.on_commit = on_commit
        self.review_log = Path(review_log) if review_log else None
        self._lock = threading.RLock()
        # Index of active relations: (head_lower, rel, tail_lower) → RelationRecord
        self._rel_index: dict[tuple[str, str, str], RelationRecord] = {}
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        """Best-effort index from store if it exposes relations; else empty."""
        self._rel_index.clear()
        # InMemoryGraphStore uses ``_relations`` list; Protocol has no list API
        rels = getattr(self.store, "relations", None) or getattr(self.store, "_relations", None)
        if isinstance(rels, dict):
            values = list(rels.values())
        elif isinstance(rels, list):
            values = rels
        else:
            values = []
        for r in values:
            key = (
                (r.head_name or "").lower(),
                r.type,
                (r.tail_name or "").lower(),
            )
            self._rel_index[key] = r

    def detect_conflicts(self, triples: list[Triple]) -> tuple[list[Triple], list[Conflict]]:
        """Split into clean inserts vs conflicts against existing edges."""
        clean: list[Triple] = []
        conflicts: list[Conflict] = []
        for t in triples:
            key = (t.head.name.lower(), t.relation, t.tail.name.lower())
            existing = self._rel_index.get(key)
            if existing is None:
                # Same head+rel but different tail → value conflict
                value_conflicts = [
                    r
                    for (h, rel, _), r in self._rel_index.items()
                    if h == t.head.name.lower() and rel == t.relation
                    and r.tail_name.lower() != t.tail.name.lower()
                ]
                if value_conflicts:
                    old = value_conflicts[0]
                    action, reason = self._decide(old.confidence, t.confidence)
                    conflicts.append(
                        Conflict(
                            relation_key=f"{t.head.name}|{t.relation}|*",
                            existing=old,
                            incoming=t,
                            action=action,
                            reason=reason,
                        )
                    )
                else:
                    clean.append(t)
                continue

            # Exact same edge — refresh sources if higher conf
            if t.confidence > existing.confidence + 1e-9:
                action, reason = self._decide(existing.confidence, t.confidence)
                conflicts.append(
                    Conflict(
                        relation_key=f"{t.head.name}|{t.relation}|{t.tail.name}",
                        existing=existing,
                        incoming=t,
                        action=action,
                        reason=reason or "higher confidence refresh",
                    )
                )
            else:
                clean.append(t)  # idempotent re-assert
        return clean, conflicts

    def _decide(self, old_conf: float, new_conf: float) -> tuple[ConflictAction, str]:
        if new_conf >= old_conf + self.auto_update_margin:
            return ConflictAction.AUTO_UPDATE, "new confidence significantly higher"
        if new_conf > old_conf:
            return ConflictAction.REVIEW, "new confidence only slightly higher"
        return ConflictAction.KEEP_OLD, "existing confidence higher or equal"

    def apply_batch(
        self,
        triples: list[Triple],
        *,
        batch_id: str | None = None,
    ) -> BatchResult:
        """Gate → conflict detect → upsert accepted (no clear_first)."""
        bid = batch_id or str(uuid.uuid4())
        t0 = time.perf_counter()
        result = BatchResult(batch_id=bid)

        with self._lock:
            accepted = triples
            if self.schema is not None:
                gate = gate_triples(
                    triples,
                    self.schema,
                    confidence_threshold=self.confidence_threshold,
                )
                accepted = gate.accepted
                result.rejected = len(gate.rejected)

            clean, conflicts = self.detect_conflicts(accepted)
            to_write: list[Triple] = list(clean)
            for c in conflicts:
                if c.action == ConflictAction.AUTO_UPDATE:
                    to_write.append(c.incoming)
                    result.conflicts_auto += 1
                elif c.action == ConflictAction.REVIEW:
                    result.conflicts_review += 1
                    item = c.to_dict()
                    result.review_items.append(item)
                    self._append_review(item)
                # KEEP_OLD / SKIP: drop

            # Upsert without clearing graph
            stats = load_triples_into_graph(
                self.store,
                to_write,
                clear_first=False,
                schema=None,  # already gated
            )
            result.accepted = int(stats.get("relations", 0) or len(to_write))
            # Refresh index with written records
            ents, rels = triples_to_records(to_write)
            del ents
            for r in rels:
                key = (
                    (r.head_name or "").lower(),
                    r.type,
                    (r.tail_name or "").lower(),
                )
                self._rel_index[key] = r

            if self.on_commit is not None:
                self.on_commit()

        result.duration_ms = int((time.perf_counter() - t0) * 1000)
        return result

    def _append_review(self, item: dict[str, Any]) -> None:
        if not self.review_log:
            return
        self.review_log.parent.mkdir(parents=True, exist_ok=True)
        with self.review_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
