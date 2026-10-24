"""Incremental graph update with conflict detection (FR-KG-05 / P3-KG-01).

New docs → extract → conflict detect → high-conf auto-merge / low-conf review
→ index sync. Online queries keep reading while batches commit.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agentic_graphrag.core.constants import MILLISECONDS_PER_SECOND
from agentic_graphrag.core.events import EventBus, GraphCommitted
from agentic_graphrag.core.ports.capabilities import (
    DEFAULT_RELATION_SCAN,
    RelationDeletion,
    RelationLister,
)
from agentic_graphrag.core.ports.stores import GraphStore, RelationDeleter, RelationRecord
from agentic_graphrag.knowledge.cardinality import sides_resolver
from agentic_graphrag.knowledge.graph_builder import (
    load_triples_into_graph,
    stamp_tenant,
    triples_to_records,
)
from agentic_graphrag.knowledge.incremental_conflicts import (
    Conflict,
    ConflictAction,
    RelIndex,
    index_key,
    split_by_conflict,
    triple_key,
)
from agentic_graphrag.knowledge.review.queue import ReviewQueue
from agentic_graphrag.knowledge.schema_check import (
    SchemaDefinition,
    Triple,
    default_confidence_threshold,
    default_schema,
    gate_triples,
)
from agentic_graphrag.knowledge.temporal import VALID_FROM, closed

logger = logging.getLogger(__name__)


TEMPORAL_ACTIONS = frozenset({ConflictAction.SUCCEED, ConflictAction.HISTORICAL})

# Re-exported so callers keep importing conflict types from this module.
__all__ = ["BatchResult", "Conflict", "ConflictAction", "IncrementalUpdater"]


@dataclass
class BatchResult:
    batch_id: str
    accepted: int = 0
    rejected: int = 0
    conflicts_auto: int = 0
    conflicts_review: int = 0
    conflicts_kept: int = 0
    conflicts_temporal: int = 0
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
            "conflicts_kept": self.conflicts_kept,
            "conflicts_temporal": self.conflicts_temporal,
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
        confidence_threshold: float | None = None,
        auto_update_margin: float = 0.15,
        on_commit: Callable[[], None] | None = None,
        events: EventBus | None = None,
        review_log: Path | str | None = None,
        review_queue: ReviewQueue | None = None,
        tenant_id: str = "",
    ) -> None:
        self.store = store
        # BL-03: conflicts needing a human land in the same ``ReviewQueue`` the
        # API serves, not only in a side JSONL nobody reads.
        self.review_queue = review_queue
        # Conflicts are scoped to one tenant: a tenant upload must never retire
        # another tenant's (or the shared seed's) edges.
        self.tenant_id = tenant_id
        # ``None`` means "use the configured gate". The incremental path used to
        # treat it as "no gate", so new triples entered the graph with neither
        # schema nor confidence validation (docs/design/BUSINESS_LOGIC.md BL-07).
        self.schema = schema if schema is not None else default_schema()
        self.confidence_threshold = (
            default_confidence_threshold() if confidence_threshold is None else confidence_threshold
        )
        self.auto_update_margin = auto_update_margin
        self.on_commit = on_commit  # legacy hook; prefer ``events``
        self.events = events
        self.review_log = Path(review_log) if review_log else None
        self._lock = threading.RLock()
        # Index of active relations: (head_lower, rel, tail_lower) → RelationRecord
        self._rel_index: RelIndex = {}
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        """Best-effort index from store (memory list or Neo4j list_relations)."""
        self._rel_index.clear()
        for r in self._iter_store_relations():
            if (r.tenant_id or "") != self.tenant_id:
                continue
            self._rel_index[index_key(r.head_name, r.type, r.tail_name)] = r

    def _iter_store_relations(self) -> list[RelationRecord]:
        if isinstance(self.store, RelationLister):
            try:
                return self.store.list_relations(limit=DEFAULT_RELATION_SCAN)
            except Exception:
                logger.warning("list_relations failed; conflict index starts empty", exc_info=True)
                return []
        return []

    def detect_conflicts(self, triples: list[Triple]) -> tuple[list[Triple], list[Conflict]]:
        """Split into clean inserts vs conflicts against existing edges."""
        return split_by_conflict(
            triples,
            self._rel_index,
            margin=self.auto_update_margin,
            sides_for=sides_resolver(self.schema),
        )

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
        for triple in triples:
            stamp_tenant(triple, self.tenant_id)
        with self._lock:
            self._apply_locked(triples, result)
        result.duration_ms = int((time.perf_counter() - t0) * MILLISECONDS_PER_SECOND)
        return result

    def _apply_locked(self, triples: list[Triple], result: BatchResult) -> None:
        accepted = self._gate(triples, result)
        clean, conflicts = self.detect_conflicts(accepted)
        to_write = self._collect_writes(clean, conflicts, result)
        # ``pre_gated``: ``_gate`` already applied schema + confidence, so
        # re-validating here would double-count rejections.
        stats = load_triples_into_graph(self.store, to_write, clear_first=False, pre_gated=True)
        # Report what the store actually upserted. The old fallback to
        # ``len(to_write)`` reported planned writes as accepted when the store
        # wrote nothing (docs/design/BUSINESS_LOGIC.md BL-11).
        result.accepted = int(stats.get("relations_upserted", 0))
        self._refresh_index(to_write)
        if self.events is not None:
            self.events.publish(
                GraphCommitted(
                    tenant_id=self.tenant_id,
                    source="incremental",
                    relations_written=result.accepted,
                )
            )
        if self.on_commit is not None:
            self.on_commit()

    def _gate(self, triples: list[Triple], result: BatchResult) -> list[Triple]:
        gate = gate_triples(triples, self.schema, confidence_threshold=self.confidence_threshold)
        result.rejected = len(gate.rejected)
        return gate.accepted

    def _collect_writes(
        self,
        clean: list[Triple],
        conflicts: list[Conflict],
        result: BatchResult,
    ) -> list[Triple]:
        to_write: list[Triple] = list(clean)
        seen = {triple_key(t) for t in clean}
        for c in conflicts:
            self._count_conflict(c, result)
            if c.action in TEMPORAL_ACTIONS:
                self._close_superseded(c)
            elif c.action == ConflictAction.AUTO_UPDATE:
                self._retire_conflicting_edges(c)
            else:
                continue
            key = triple_key(c.incoming)
            if key not in seen:
                seen.add(key)
                to_write.append(c.incoming)
        return to_write

    def _count_conflict(self, conflict: Conflict, result: BatchResult) -> None:
        """Every conflict lands in exactly one counter so batch numbers conserve."""
        if conflict.action == ConflictAction.AUTO_UPDATE:
            result.conflicts_auto += 1
        elif conflict.action in TEMPORAL_ACTIONS:
            result.conflicts_temporal += 1
        elif conflict.action == ConflictAction.REVIEW:
            result.conflicts_review += 1
            item = conflict.to_dict()
            result.review_items.append(item)
            self._append_review(item, result.batch_id)
        else:  # KEEP_OLD — previously neither written nor counted (BL-11)
            result.conflicts_kept += 1

    def _retire_conflicting_edges(self, conflict: Conflict) -> None:
        """Remove every superseded edge so AUTO_UPDATE does not leave dual facts."""
        for old in conflict.edges_to_retire():
            self._rel_index.pop(index_key(old.head_name, old.type, old.tail_name), None)
            self._delete_relation(old)

    def _close_superseded(self, conflict: Conflict) -> None:
        """BL-14: end older facts at the incoming start instead of deleting them."""
        start = conflict.incoming.attributes.get(VALID_FROM)
        updated = [
            replace(old, attributes=closed(old.attributes, start)) for old in conflict.superseded
        ]
        if updated:
            self.store.upsert_relations(updated)
        for rec in updated:
            self._rel_index[index_key(rec.head_name, rec.type, rec.tail_name)] = rec

    def _delete_relation(self, old: RelationRecord) -> None:
        if isinstance(self.store, RelationDeletion):
            self._call_deleter(self.store.delete_relation, old)
            return
        logger.warning("store cannot delete relations; %s survives as a dual fact", old.id)

    def _call_deleter(self, deleter: RelationDeleter, old: RelationRecord) -> None:
        try:
            deleter(old.id, tenant_id=old.tenant_id)
        except Exception:
            # Swallowing this left the old edge in the graph while the new one
            # was written — two contradictory facts, silently (BL-10).
            logger.warning("delete_relation(%s) failed; dual fact may remain", old.id)

    def _refresh_index(self, to_write: list[Triple]) -> None:
        _ents, rels = triples_to_records(to_write)
        del _ents
        for r in rels:
            self._rel_index[index_key(r.head_name, r.type, r.tail_name)] = r

    def _append_review(self, item: dict[str, Any], batch_id: str) -> None:
        if self.review_queue is not None:
            from agentic_graphrag.knowledge.review.queue import ReviewType

            self.review_queue.enqueue(
                ReviewType.CONFLICT,
                item,
                confidence=float(item["incoming"].get("confidence") or 0.0),
                batch_id=batch_id,
                tenant_id=self.tenant_id,
            )
        if not self.review_log:
            return
        self.review_log.parent.mkdir(parents=True, exist_ok=True)
        with self.review_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
