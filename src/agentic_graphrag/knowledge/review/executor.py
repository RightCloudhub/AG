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
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from agentic_graphrag.core.events import EventBus, GraphCommitted, ReviewDecided
from agentic_graphrag.core.ports.entities import EntitySemanticIndex
from agentic_graphrag.core.ports.stores import DocStore, GraphStore
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph, triples_to_records
from agentic_graphrag.knowledge.review.document_guard import stale_document
from agentic_graphrag.knowledge.review.executor_ops import (
    delete_relations,
    relations_from_docs,
)
from agentic_graphrag.knowledge.review.queue import ReviewItem, ReviewStatus, ReviewType
from agentic_graphrag.knowledge.schema_check import SchemaDefinition, Triple

logger = logging.getLogger(__name__)

Handler = Callable[[ReviewItem], dict[str, Any]]


@dataclass
class ReviewExecutorDeps:
    graph: GraphStore
    review_queue: Any | None = None
    task_store: Any | None = None
    on_commit: Callable[[], None] | None = None  # legacy hook; prefer ``events``
    events: EventBus | None = None
    docs: DocStore | None = None
    schema: SchemaDefinition | None = None
    entity_index: EntitySemanticIndex | None = None
    embedding_space: str = ""
    before_done: Callable[[str], None] | None = None


class ReviewExecutor:
    """Apply decided review items to the graph, idempotently."""

    def __init__(self, deps: ReviewExecutorDeps) -> None:
        self.graph = deps.graph
        self.review_queue = deps.review_queue
        self.task_store = deps.task_store
        self.on_commit = deps.on_commit
        self.events = deps.events
        self.docs = deps.docs
        self.schema = deps.schema
        self.entity_index = deps.entity_index
        self.embedding_space = deps.embedding_space
        self.before_done = deps.before_done
        self._handlers: dict[tuple[str, str], Handler] = {
            (ReviewType.CONFLICT.value, ReviewStatus.APPROVED.value): self._approve_conflict,
            (ReviewType.EXTRACTION.value, ReviewStatus.APPROVED.value): self._write_proposed,
            (ReviewType.EXTRACTION.value, ReviewStatus.REJECTED.value): self._remove_proposed,
            (ReviewType.RESOLUTION.value, ReviewStatus.APPROVED.value): self._merge_entities,
            (ReviewType.SPOTCHECK.value, ReviewStatus.REJECTED.value): self._rollback_upload,
        }

    def execute(self, item: ReviewItem) -> dict[str, Any]:
        """Run the handler for ``item``'s (type, status); record and return the outcome."""
        with self.docs.lock() if self.docs is not None else nullcontext():
            return self._execute_locked(item)

    def _execute_locked(self, item: ReviewItem) -> dict[str, Any]:
        if item.execution.get("applied") or item.execution.get("completed"):
            self._settle_task(item)
            return item.execution  # idempotent: a replay never writes twice
        handler = self._handlers.get((item.type, item.status))
        if self.docs is not None and stale_document(item, self.docs, tasks=self.task_store):
            outcome = {"applied": False, "effect": "stale_document"}
        else:
            outcome = {"applied": False, "effect": "none"} if handler is None else handler(item)
        outcome["completed"] = True
        applied = bool(outcome.get("applied"))
        self._publish_events(item, applied=applied)
        if self.review_queue is not None:
            self.review_queue.record_execution(item.id, outcome)
        item.execution = outcome
        self._settle_task(item)
        return outcome

    def _publish_events(self, item: ReviewItem, *, applied: bool) -> None:
        if self.events is not None:
            tenant = str(item.tenant_id or "")
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
        if item.payload.get("staged"):
            return {"applied": False, "effect": "candidate_rejected"}
        triple = _triple(item.payload.get("triple"), item.tenant_id)
        if triple is None:
            return {"applied": False, "effect": "invalid_payload"}
        _ents, rels = triples_to_records([triple])
        removed = delete_relations(self.graph, [r.id for r in rels], tenant_id=item.tenant_id)
        return {"applied": bool(removed), "effect": "fact_removed", "retired": removed}

    def _merge_entities(self, item: ReviewItem) -> dict[str, Any]:
        if item.payload.get("open_resolution"):
            return self._merge_open_entities(item)
        from agentic_graphrag.knowledge.resolution_merge import MergeApply, apply_entity_merge

        keep = self.graph.get_entity_by_name(
            str(item.payload.get("keep") or ""), tenant_id=item.tenant_id
        )
        drop = self.graph.get_entity_by_name(
            str(item.payload.get("drop") or ""), tenant_id=item.tenant_id
        )
        if keep is None or drop is None or keep.id == drop.id:
            return {"applied": False, "effect": "entities_not_found"}
        canonical = str(item.payload.get("canonical") or keep.name)
        apply_entity_merge(MergeApply(store=self.graph, keep=keep, drop=drop, canonical=canonical))
        return {"applied": True, "effect": "entities_merged", "kept": canonical}

    def _merge_open_entities(self, item: ReviewItem) -> dict[str, Any]:
        from agentic_graphrag.knowledge.review.open_resolution import (
            OpenMergeDeps,
            apply_open_merge,
        )

        if self.entity_index is None or self.review_queue is None or not self.embedding_space:
            return {"applied": False, "effect": "entity_index_unavailable"}
        return apply_open_merge(
            item,
            OpenMergeDeps(
                graph=self.graph,
                index=self.entity_index,
                space=self.embedding_space,
                queue=self.review_queue,
            ),
        )

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
        stats = load_triples_into_graph(self.graph, [triple], clear_first=False, schema=self.schema)
        return int(stats.get("relations_upserted", 0))

    def _settle_task(self, item: ReviewItem) -> None:
        """REVIEW → DONE once no conflict of the item's ingest batch is pending."""
        from agentic_graphrag.knowledge.review.settlement import ready_review_task

        if not ready_review_task(self, item):
            return
        from agentic_graphrag.knowledge.ingest_tasks import IngestStatus

        if self.before_done is not None:
            self.before_done(item.batch_id)

        try:
            assert self.task_store is not None
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
