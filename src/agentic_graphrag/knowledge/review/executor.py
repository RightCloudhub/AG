"""Review decision executor (docs/design/BUSINESS_LOGIC.md BL-03).

``ReviewQueue.decide`` records *what* a reviewer chose; this module makes the
choice take effect on the knowledge graph:

| item type  | approve                                   | reject                              |
|------------|-------------------------------------------|-------------------------------------|
| conflict   | write the incoming fact, retire the old    | keep the existing fact (no write)   |
| extraction | write the proposed fact                    | remove the fact if it was written   |
| resolution | merge ``drop`` into ``keep``               | keep both entities                  |
| spotcheck  | accept the upload as is                    | roll back facts sourced from it     |
| feedback   | no graph effect (answer-quality signal)    | no graph effect                     |

Every execution is recorded on the item (``execution``) and, once the last
conflict of an ingest batch is decided, the task leaves ``review`` for ``done``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agentic_graphrag.core.events import EventBus, GraphCommitted, ReviewDecided
from agentic_graphrag.core.ports.stores import GraphStore
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph, triples_to_records
from agentic_graphrag.knowledge.review.executor_ops import (
    delete_relations,
    relations_from_docs,
)
from agentic_graphrag.knowledge.review.queue import ReviewItem, ReviewStatus, ReviewType
from agentic_graphrag.knowledge.schema_check import Triple

logger = logging.getLogger(__name__)

Handler = Callable[[ReviewItem], dict[str, Any]]


@dataclass
class ReviewExecutorDeps:
    graph: GraphStore
    review_queue: Any | None = None
    task_store: Any | None = None
    on_commit: Callable[[], None] | None = None  # legacy hook; prefer ``events``
    events: EventBus | None = None


class ReviewExecutor:
    """Apply decided review items to the graph, idempotently."""

    def __init__(self, deps: ReviewExecutorDeps) -> None:
        self.graph = deps.graph
        self.review_queue = deps.review_queue
        self.task_store = deps.task_store
        self.on_commit = deps.on_commit
        self.events = deps.events
        self._handlers: dict[tuple[str, str], Handler] = {
            (ReviewType.CONFLICT.value, ReviewStatus.APPROVED.value): self._approve_conflict,
            (ReviewType.EXTRACTION.value, ReviewStatus.APPROVED.value): self._write_proposed,
            (ReviewType.EXTRACTION.value, ReviewStatus.REJECTED.value): self._remove_proposed,
            (ReviewType.RESOLUTION.value, ReviewStatus.APPROVED.value): self._merge_entities,
            (ReviewType.SPOTCHECK.value, ReviewStatus.REJECTED.value): self._rollback_upload,
        }

    def execute(self, item: ReviewItem) -> dict[str, Any]:
        """Run the handler for ``item``'s (type, status); record and return the outcome."""
        if item.execution.get("applied"):
            return item.execution  # idempotent: a replay never writes twice
        handler = self._handlers.get((item.type, item.status))
        outcome = {"applied": False, "effect": "none"} if handler is None else handler(item)
        applied = bool(outcome.get("applied"))
        if self.events is not None:
            tenant = str(getattr(item, "tenant_id", "") or "")
            if applied:
                self.events.publish(GraphCommitted(tenant_id=tenant, source="review"))
            self.events.publish(
                ReviewDecided(
                    tenant_id=tenant,
                    review_id=item.id,
                    review_type=str(item.type),
                    decision=str(item.status),
                    applied=applied,
                )
            )
        if applied and self.on_commit is not None:
            self.on_commit()
        if self.review_queue is not None:
            self.review_queue.record_execution(item.id, outcome)
        item.execution = outcome
        self._settle_task(item)
        return outcome

    # --- handlers ---------------------------------------------------------

    def _approve_conflict(self, item: ReviewItem) -> dict[str, Any]:
        retire = [r["id"] for r in item.payload.get("superseded") or [] if r and r.get("id")]
        removed = delete_relations(self.graph, retire, tenant_id=item.tenant_id)
        written = self._write(item.payload.get("incoming"), item.tenant_id)
        return {"applied": True, "effect": "fact_replaced", "retired": removed, "written": written}

    def _write_proposed(self, item: ReviewItem) -> dict[str, Any]:
        written = self._write(item.payload.get("triple"), item.tenant_id)
        return {"applied": bool(written), "effect": "fact_written", "written": written}

    def _remove_proposed(self, item: ReviewItem) -> dict[str, Any]:
        triple = _triple(item.payload.get("triple"), item.tenant_id)
        if triple is None:
            return {"applied": False, "effect": "invalid_payload"}
        _ents, rels = triples_to_records([triple])
        removed = delete_relations(self.graph, [r.id for r in rels], tenant_id=item.tenant_id)
        return {"applied": bool(removed), "effect": "fact_removed", "retired": removed}

    def _merge_entities(self, item: ReviewItem) -> dict[str, Any]:
        from agentic_graphrag.knowledge.resolution_merge import MergeApply, apply_entity_merge

        keep = self.graph.get_entity_by_name(str(item.payload.get("keep") or ""))
        drop = self.graph.get_entity_by_name(str(item.payload.get("drop") or ""))
        if keep is None or drop is None or keep.id == drop.id:
            return {"applied": False, "effect": "entities_not_found"}
        canonical = str(item.payload.get("canonical") or keep.name)
        apply_entity_merge(MergeApply(store=self.graph, keep=keep, drop=drop, canonical=canonical))
        return {"applied": True, "effect": "entities_merged", "kept": canonical}

    def _rollback_upload(self, item: ReviewItem) -> dict[str, Any]:
        doc_ids = set(item.payload.get("doc_ids") or [])
        if not doc_ids:
            return {"applied": False, "effect": "no_documents"}
        ids = relations_from_docs(self.graph, doc_ids, tenant_id=item.tenant_id)
        removed = delete_relations(self.graph, ids, tenant_id=item.tenant_id)
        return {"applied": bool(removed), "effect": "upload_rolled_back", "retired": removed}

    # --- helpers ----------------------------------------------------------

    def _write(self, raw: Any, tenant_id: str) -> int:
        triple = _triple(raw, tenant_id)
        if triple is None:
            return 0
        stats = load_triples_into_graph(self.graph, [triple], clear_first=False)
        return int(stats.get("relations_upserted", 0))

    def _settle_task(self, item: ReviewItem) -> None:
        """REVIEW → DONE once no conflict of the item's ingest batch is pending."""
        if self.task_store is None or self.review_queue is None or not item.batch_id:
            return
        task = self.task_store.get(item.batch_id)
        if task is None or task.status != "review":
            return
        pending = self.review_queue.count(
            status=ReviewStatus.PENDING.value,
            type=ReviewType.CONFLICT.value,
            batch_id=item.batch_id,
        )
        if pending:
            return
        from agentic_graphrag.knowledge.ingest_tasks import IngestStatus

        try:
            self.task_store.transition(item.batch_id, IngestStatus.DONE, message="Review completed")
        except (KeyError, ValueError) as exc:
            logger.warning("Cannot settle task %s after review: %s", item.batch_id, exc)


def _triple(raw: Any, tenant_id: str) -> Triple | None:
    if not isinstance(raw, dict):
        return None
    try:
        triple = Triple.model_validate(raw)
    except ValueError:
        return None
    if tenant_id:
        triple.attributes = {**(triple.attributes or {}), "tenant_id": tenant_id}
    return triple
