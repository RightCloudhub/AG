"""Fence review effects against deleted/replaced source documents."""

from typing import Any

from agentic_graphrag.core.knowledge import SourceRef
from agentic_graphrag.core.ports.stores import DocStore
from agentic_graphrag.knowledge.review.queue import ReviewItem


def stale_document(item: ReviewItem, docs: DocStore, *, tasks: Any = None) -> bool:
    if _stale_source_refs(item, docs) or _stale_document_versions(item, docs, tasks=tasks):
        return True
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


def _stale_document_versions(item: ReviewItem, docs: DocStore, *, tasks: Any) -> bool:
    versions = item.payload.get("doc_versions") or {}
    for doc_id in item.payload.get("doc_ids", []):
        doc = docs.get(doc_id, tenant_id=item.tenant_id or "default")
        version = versions.get(doc_id) or _task_version(item, doc_id, tasks=tasks) or 1
        if doc is None or doc.metadata.get("lifecycle") or str(version) != str(doc.version):
            return True
    return False


def _stale_source_refs(item: ReviewItem, docs: DocStore) -> bool:
    for raw_source in item.payload.get("source_refs", []):
        source = SourceRef.model_validate(raw_source)
        doc = docs.get(source.doc_id, tenant_id=item.tenant_id or "default")
        if (
            source.scope.tenant_id != (item.tenant_id or "default")
            or doc is None
            or doc.kb_id != source.scope.kb_id
            or doc.version != source.doc_version
            or doc.metadata.get("lifecycle")
        ):
            return True
    return False


def _task_version(item: ReviewItem, doc_id: str, *, tasks: Any) -> Any:
    if tasks is None or not item.batch_id:
        return None
    task = tasks.get(item.batch_id, tenant_id=item.tenant_id or "default")
    rows = task.docs if task else []
    return next((r.get("version") for r in rows if r.get("doc_id") == doc_id), None)
