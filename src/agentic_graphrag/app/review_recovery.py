"""Rebuild approved staged facts without rerunning extraction or human decisions."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.knowledge.review.executor import ReviewExecutor, ReviewExecutorDeps
from agentic_graphrag.knowledge.review.queue import ReviewItem

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def restore_required_reviews(svc: QueryService, *, batch_id: str) -> None:
    from agentic_graphrag.app.graph_wiring import schema_for_service

    kb, queue = svc.knowledge_base, svc.review_queue
    if kb is None or kb.build_mode != "schema" or kb.review_policy != "required" or queue is None:
        return
    with svc.bundle.docs.lock():
        approvals = _approvals(svc, batch_id=batch_id)
        executor = ReviewExecutor(
            ReviewExecutorDeps(
                graph=svc.bundle.graph, docs=svc.bundle.docs, schema=schema_for_service(svc)
            )
        )
        for doc_id in svc.bundle.docs.list_ids(tenant_id=kb.tenant_id):
            doc = svc.bundle.docs.get(doc_id, tenant_id=kb.tenant_id)
            if doc is not None and not doc.metadata.get("lifecycle"):
                _restore_document(svc, doc, approvals=approvals, executor=executor)


def _approvals(svc: QueryService, *, batch_id: str) -> list[ReviewItem]:
    kb, queue = svc.knowledge_base, svc.review_queue
    assert kb is not None and queue is not None
    _, total = queue.list_page(status="approved", tenant_id=kb.tenant_id, limit=1)
    return [
        item
        for item in queue.list(status="approved", tenant_id=kb.tenant_id, limit=max(1, total))
        if item.batch_id == batch_id
        and item.payload.get("target_generation") == kb.active_generation
        and item.payload.get("staged")
        and item.execution.get("applied")
    ]


def _restore_document(
    svc: QueryService,
    doc: DocumentRecord,
    *,
    approvals: list[ReviewItem],
    executor: ReviewExecutor,
) -> None:
    chunks = [chunk for chunk in doc.chunks if chunk.metadata.get("graph_recovery_pending")]
    if not chunks:
        return
    assert svc.knowledge_base is not None
    if svc.knowledge_store and svc.knowledge_store.is_tombstoned(
        svc.knowledge_base.scope, doc.doc_id, version=doc.version
    ):
        return
    _replay_approvals(doc, approvals, executor=executor)
    for chunk in chunks:
        chunk.graph_extracted = True
        chunk.metadata.pop("graph_recovery_pending", None)
    doc.metadata["index_complete"] = bool(doc.metadata.pop("graph_recovery_index_complete", False))
    svc.bundle.docs.save(doc)


def _replay_approvals(
    doc: DocumentRecord, approvals: list[ReviewItem], *, executor: ReviewExecutor
) -> None:
    for item in approvals:
        raw = item.payload.get("triple") or {}
        if raw.get("source_doc_id") == doc.doc_id:
            # Receipts remain unchanged; only their process-local graph effects
            # are replayed. The executor still fences stale document versions.
            executor.execute(replace(item, execution={}))
