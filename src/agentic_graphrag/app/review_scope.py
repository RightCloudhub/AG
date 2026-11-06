"""Execute review against its pending generation, then publish a completed batch."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic_graphrag.app.entity_generations import carry_entities
from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.app.knowledge_writes import _legacy_write, prepare_upload, publish_task
from agentic_graphrag.knowledge.review.queue import ReviewItem

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def review_service(svc: QueryService, item: ReviewItem) -> QueryService:
    kb = svc.knowledge_base
    if kb is None:
        return svc
    if item.payload.get("kb_id", "default") != kb.kb_id:
        raise KeyError(item.id)
    generation = str(item.payload.get("target_generation") or kb.active_generation)
    return select_service(svc, tenant_id=kb.tenant_id, kb_id=kb.kb_id, generation=generation)


def publish_reviewed_batch(svc: QueryService, item: ReviewItem) -> None:
    kb = svc.knowledge_base
    if kb is not None and knowledge_store(svc).get_write(kb.scope, f"review-{item.id}"):
        publish_task(svc, f"review-{item.id}")
        return
    if svc.ingest_tasks is None or not item.batch_id:
        return
    task = svc.ingest_tasks.get(item.batch_id, tenant_id=item.tenant_id or "default")
    if task is not None and task.status == "done":
        publish_task(svc, task.id)


def review_write_service(svc: QueryService, item: ReviewItem) -> QueryService:
    selected = review_service(svc, item)
    kb = selected.knowledge_base
    if kb is None or _legacy_write(selected):
        return selected
    root = svc.parent or svc
    metadata = knowledge_store(root)
    intent = metadata.get_write(kb.scope, item.batch_id) if item.batch_id else None
    if intent is not None and intent.status == "pending":
        return prepare_upload(selected, intent.operation_id)
    if metadata.get_snapshot(kb.scope, kb.active_generation) is None:
        return selected
    current = select_service(root, tenant_id=kb.tenant_id, kb_id=kb.kb_id)
    shadow = prepare_upload(current, f"review-{item.id}")
    if item.payload.get("open_resolution"):
        carry_entities(shadow)
    return shadow
