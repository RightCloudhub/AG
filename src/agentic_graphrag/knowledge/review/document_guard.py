"""Fence review effects against deleted/replaced source documents."""

from typing import Any

from agentic_graphrag.core.ports.stores import DocStore
from agentic_graphrag.knowledge.review.queue import ReviewItem


def stale_document(item: ReviewItem, docs: DocStore, *, tasks: Any = None) -> bool:
    raw = item.payload.get("incoming") or item.payload.get("triple")
    if not isinstance(raw, dict) or not raw.get("source_doc_id"):
        return False
    doc_id = str(raw["source_doc_id"])
    doc = docs.get(doc_id, tenant_id=item.tenant_id or "default")
    if doc is None or doc.metadata.get("lifecycle"):
        return True
    version = raw.get("source_doc_version") or _task_version(item, doc_id, tasks=tasks)
    # Legacy reviews without a version can only be trusted for an unreplaced document.
    return str(version if version is not None else 1) != str(doc.version)


def _task_version(item: ReviewItem, doc_id: str, *, tasks: Any) -> Any:
    if tasks is None or not item.batch_id:
        return None
    task = tasks.get(item.batch_id, tenant_id=item.tenant_id or "default")
    rows = task.docs if task else []
    return next((r.get("version") for r in rows if r.get("doc_id") == doc_id), None)
