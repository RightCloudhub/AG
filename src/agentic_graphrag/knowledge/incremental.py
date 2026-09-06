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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph, triples_to_records
from agentic_graphrag.knowledge.incremental_conflicts import (
    Conflict,
    ConflictAction,
    RelIndex,
    index_key,
    split_by_conflict,
    triple_key,
)
from agentic_graphrag.knowledge.review.queue import ReviewQueue, ReviewType
from agentic_graphrag.knowledge.schema_check import (
    SchemaDefinition,
    Triple,
    default_confidence_threshold,
    default_schema,
    gate_triples,
)
from agentic_graphrag.stores.interfaces import GraphStore, RelationRecord

logger = logging.getLogger(__name__)

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
    review_items: list[dict[str, Any]] = field(default_factory=list)
    review_item_ids: list[str] = field(default_factory=list)
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
            "review_items": self.review_items,
            "review_item_ids": self.review_item_ids,
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
        review_log: Path | str | None = None,
        review_queue: ReviewQueue | None = None,
    ) -> None:
        self.store = store
        # ``None`` means "use the configured gate". The incremental path used to
        # treat it as "no gate", so new triples entered the graph with neither
        # schema nor confidence validation (docs/BUSINESS_LOGIC.md BL-07).
        self.schema = schema if schema is not None else default_schema()
        self.confidence_threshold = (
            default_confidence_threshold() if confidence_threshold is None else confidence_threshold
        )
        self.auto_update_margin = auto_update_margin
        self.on_commit = on_commit
        self.review_log = Path(review_log) if review_log else None
        # When wired, REVIEW conflicts also land in the API-visible review
        # queue — the legacy JSONL ``review_log`` was invisible to
        # ``GET /v1/review-queue`` (docs/BUSINESS_LOGIC.md BL-03).
        self.review_queue = review_queue
        self._lock = threading.RLock()
        # Index of active relations: (head_lower, rel, tail_lower) → RelationRecord
        self._rel_index: RelIndex = {}
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        """Best-effort index from store (memory list or Neo4j list_relations)."""
        self._rel_index.clear()
        for r in self._iter_store_relations():
            self._rel_index[index_key(r.head_name, r.type, r.tail_name)] = r

    def _iter_store_relations(self) -> list[RelationRecord]:
        rels = getattr(self.store, "relations", None) or getattr(self.store, "_relations", None)
        if isinstance(rels, dict):
            return list(rels.values())
        if isinstance(rels, list):
            return list(rels)
        lister = getattr(self.store, "list_relations", None)
        if callable(lister):
            try:
                return list(lister(limit=50_000) or [])
            except Exception:
                logger.warning("list_relations failed; conflict index starts empty", exc_info=True)
                return []
        return []

    def detect_conflicts(self, triples: list[Triple]) -> tuple[list[Triple], list[Conflict]]:
        """Split into clean inserts vs conflicts against existing edges."""
        return split_by_conflict(triples, self._rel_index, margin=self.auto_update_margin)

    def apply_batch(
        self,
        triples: list[Triple],
        *,
        batch_id: str | None = None,
        tenant_id: str = "",
    ) -> BatchResult:
        """Gate → conflict detect → upsert accepted (no clear_first)."""
        bid = batch_id or str(uuid.uuid4())
        t0 = time.perf_counter()
        result = BatchResult(batch_id=bid)
        with self._lock:
            self._apply_locked(triples, result, tenant_id=tenant_id)
        result.duration_ms = int((time.perf_counter() - t0) * 1000)
        return result

    def _apply_locked(
        self, triples: list[Triple], result: BatchResult, *, tenant_id: str = ""
    ) -> None:
        accepted = self._gate(triples, result)
        clean, conflicts = self.detect_conflicts(accepted)
        to_write = self._collect_writes(clean, conflicts, result, tenant_id=tenant_id)
        # ``pre_gated``: ``_gate`` already applied schema + confidence, so
        # re-validating here would double-count rejections.
        stats = load_triples_into_graph(self.store, to_write, clear_first=False, pre_gated=True)
        # Report what the store actually upserted. The old fallback to
        # ``len(to_write)`` reported planned writes as accepted when the store
        # wrote nothing (docs/BUSINESS_LOGIC.md BL-11).
        result.accepted = int(stats.get("relations_upserted", 0))
        self._refresh_index(to_write)
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
        *,
        tenant_id: str = "",
    ) -> list[Triple]:
        to_write: list[Triple] = list(clean)
        seen = {triple_key(t) for t in clean}
        for c in conflicts:
            self._count_conflict(c, result, tenant_id=tenant_id)
            if c.action != ConflictAction.AUTO_UPDATE:
                continue
            self._retire_conflicting_edges(c)
            key = triple_key(c.incoming)
            if key not in seen:
                seen.add(key)
                to_write.append(c.incoming)
        return to_write

    def _count_conflict(
        self, conflict: Conflict, result: BatchResult, *, tenant_id: str = ""
    ) -> None:
        """Every conflict lands in exactly one counter so batch numbers conserve."""
        if conflict.action == ConflictAction.AUTO_UPDATE:
            result.conflicts_auto += 1
        elif conflict.action == ConflictAction.REVIEW:
            result.conflicts_review += 1
            item = conflict.to_dict()
            result.review_items.append(item)
            self._append_review(item)
            if self.review_queue is not None:
                queued = self.review_queue.enqueue(
                    ReviewType.CONFLICT,
                    item,
                    confidence=conflict.incoming.confidence,
                    batch_id=result.batch_id,
                    tenant_id=tenant_id,
                )
                result.review_item_ids.append(queued.id)
        else:  # KEEP_OLD — previously neither written nor counted (BL-11)
            result.conflicts_kept += 1

    def _retire_conflicting_edges(self, conflict: Conflict) -> None:
        """Remove every superseded edge so AUTO_UPDATE does not leave dual facts."""
        for old in conflict.edges_to_retire():
            self._rel_index.pop(index_key(old.head_name, old.type, old.tail_name), None)
            self._delete_relation(old)

    def _delete_relation(self, old: RelationRecord) -> None:
        deleter = getattr(self.store, "delete_relation", None)
        if callable(deleter):
            self._call_deleter(deleter, old)
            return
        if self._drop_from_list(old):
            return
        logger.warning("store cannot delete relations; %s survives as a dual fact", old.id)

    def _call_deleter(self, deleter: Callable[[str], Any], old: RelationRecord) -> None:
        try:
            deleter(old.id)
        except Exception:
            # Swallowing this left the old edge in the graph while the new one
            # was written — two contradictory facts, silently (BL-10).
            logger.warning("delete_relation(%s) failed; dual fact may remain", old.id)

    def _drop_from_list(self, old: RelationRecord) -> bool:
        """Fallback for stores holding relations in a plain list attribute."""
        for attr in ("relations", "_relations"):
            rels = getattr(self.store, attr, None)
            if isinstance(rels, list):
                setattr(self.store, attr, [r for r in rels if r.id != old.id])
                return True
        return False

    def _refresh_index(self, to_write: list[Triple]) -> None:
        _ents, rels = triples_to_records(to_write)
        del _ents
        for r in rels:
            self._rel_index[index_key(r.head_name, r.type, r.tail_name)] = r

    def _append_review(self, item: dict[str, Any]) -> None:
        if not self.review_log:
            return
        self.review_log.parent.mkdir(parents=True, exist_ok=True)
        with self.review_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
