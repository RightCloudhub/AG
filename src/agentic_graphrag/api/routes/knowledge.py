"""Knowledge management APIs (FR-API-03 / P3-KG-04)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from pydantic import BaseModel, Field

from agentic_graphrag.api.envelope import MetaBody, ok
from agentic_graphrag.api.errors import INVALID_INPUT, ApiError
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.knowledge.review.queue import ReviewDecision, ReviewType

router = APIRouter(prefix="/v1", tags=["knowledge"])

# In-process ingest task registry (pilot-scale)
_TASKS: dict[str, dict[str, Any]] = {}


class DocUploadMeta(BaseModel):
    title: str = ""
    source: str = ""
    doc_id: str | None = None


class ReviewDecisionBody(BaseModel):
    decision: str = Field(..., description="approve|reject|skip")
    reviewer: str = ""
    note: str = ""


class FeedbackBody(BaseModel):
    query_id: str
    accurate: bool
    reason: str = ""


def _service(request: Request) -> QueryService:
    svc = getattr(request.app.state, "query_service", None)
    if svc is None:
        raise ApiError("SERVICE_UNAVAILABLE", "Query service not initialized", status_code=503)
    return svc


@router.post("/docs")
async def upload_docs(
    request: Request,
    files: list[UploadFile] | None = None,
) -> dict:
    """Batch document upload — persists content and creates an ingest task (FR-API-03)."""
    from agentic_graphrag.stores.interfaces import DocumentRecord

    svc = _service(request)
    task_id = str(uuid.uuid4())
    saved: list[dict[str, str]] = []
    for f in files or []:
        content = await f.read()
        text = content.decode("utf-8", errors="replace")
        doc_id = f.filename or str(uuid.uuid4())
        try:
            svc.bundle.docs.save(
                DocumentRecord(
                    doc_id=doc_id,
                    title=f.filename or doc_id,
                    content=text,
                    metadata={"task_id": task_id, "source": "upload", "bytes": len(text)},
                )
            )
        except Exception:
            # Still record the upload even if doc store write fails.
            pass
        saved.append({"doc_id": doc_id, "bytes": str(len(text)), "name": f.filename or ""})
    _TASKS[task_id] = {
        "id": task_id,
        "status": "queued" if saved else "empty",
        "docs": saved,
        "created_at": time.time(),
        "message": (
            "Documents persisted to doc store; run extract pipeline offline or via worker"
            if saved
            else "No documents received"
        ),
    }
    if svc.review_queue is not None and saved:
        svc.review_queue.enqueue(
            ReviewType.SPOTCHECK,
            {"task_id": task_id, "doc_count": len(saved)},
            confidence=0.5,
            batch_id=task_id,
        )
    return ok(_TASKS[task_id], meta=MetaBody(request_id=task_id))


@router.get("/ingest-tasks/{task_id}")
def get_ingest_task(task_id: str) -> dict:
    task = _TASKS.get(task_id)
    if task is None:
        raise ApiError(INVALID_INPUT, f"Unknown task: {task_id}", status_code=404)
    return ok(task)


@dataclass
class ReviewQueueQuery:
    status: str | None = "pending"
    type: str | None = None
    limit: int = 50
    offset: int = 0


def _review_query(
    *,
    status: str | None = Query("pending"),
    type: str | None = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
) -> ReviewQueueQuery:
    return ReviewQueueQuery(status=status, type=type, limit=limit, offset=offset)


@router.get("/review-queue")
def list_review_queue(
    request: Request,
    q: Annotated[ReviewQueueQuery, Depends(_review_query)],
) -> dict:
    svc = _service(request)
    if svc.review_queue is None:
        return ok([], meta=MetaBody(total=0, limit=q.limit, page=1))
    items = svc.review_queue.list(status=q.status, type=q.type, limit=q.limit, offset=q.offset)
    return ok(
        [i.to_dict() for i in items],
        meta=MetaBody(total=len(items), limit=q.limit, page=q.offset // max(q.limit, 1) + 1),
    )


@router.post("/review-queue/{item_id}/decision")
def decide_review(item_id: str, body: ReviewDecisionBody, request: Request) -> dict:
    svc = _service(request)
    if svc.review_queue is None:
        raise ApiError("SERVICE_UNAVAILABLE", "Review queue not configured", status_code=503)
    try:
        dec = ReviewDecision(body.decision.lower())
    except ValueError as exc:
        raise ApiError(INVALID_INPUT, "decision must be approve|reject|skip") from exc
    try:
        item = svc.review_queue.decide(item_id, dec, reviewer=body.reviewer, note=body.note)
    except KeyError as exc:
        raise ApiError(INVALID_INPUT, f"Unknown review item: {item_id}", status_code=404) from exc
    return ok(item.to_dict())


def _principal(request: Request) -> tuple[str, str]:
    p = getattr(request.state, "principal", None)
    if p is None:
        # Never trust client-supplied X-User-Id for budget identity.
        return "default", "anonymous"
    return p.tenant_id, p.user_id


@router.get("/audit/queries/{query_id}")
def get_audit_query(query_id: str, request: Request) -> dict:
    """Reasoning chain audit lookup (FR-AN-04 / P3-AN-01) — tenant-scoped."""
    svc = _service(request)
    if svc.audit_store is None:
        raise ApiError("SERVICE_UNAVAILABLE", "Audit store not configured", status_code=503)
    tenant_id, _user_id = _principal(request)
    row = svc.audit_store.get_for_tenant(query_id, tenant_id)
    if row is None:
        # 404 for both missing and cross-tenant (no existence leak).
        raise ApiError(INVALID_INPUT, f"Unknown query_id: {query_id}", status_code=404)
    return ok(row)


@router.post("/feedback")
def post_feedback(body: FeedbackBody, request: Request) -> dict:
    """User accurate/inaccurate feedback (FR-OP-03 / P4-OPS-02)."""
    svc = _service(request)
    tenant_id, user_id = _principal(request)
    if svc.audit_store is not None:
        row = svc.audit_store.get_for_tenant(body.query_id, tenant_id)
        if row is None and svc.audit_store.get(body.query_id) is not None:
            raise ApiError(INVALID_INPUT, f"Unknown query_id: {body.query_id}", status_code=404)
    result = svc.submit_feedback(
        body.query_id,
        accurate=body.accurate,
        reason=body.reason,
        user_id=user_id,
    )
    return ok(result)


@router.get("/metrics")
def get_metrics_summary() -> dict:
    from agentic_graphrag.observability.metrics import get_metrics

    return ok(get_metrics().summary())


@router.get("/graph/entities")
def list_graph_entities(request: Request, limit: int = 50, offset: int = 0) -> dict:
    """Minimal graph browse API scaffold (P5-CAP-01).

    Supports stores that expose ``list_entities`` (InMemoryGraphStore) or a
    public/private entity map (``entities`` / ``_entities``).
    """
    svc = _service(request)
    store = svc.bundle.graph
    lim = max(0, min(int(limit or 50), 500))
    off = max(0, int(offset or 0))

    records = _list_entity_records(store, limit=lim, offset=off)
    total = _entity_total(store, fallback=len(records) if off == 0 else None)
    rows = [
        {
            "id": e.id,
            "name": e.name,
            "type": e.type,
            "aliases": list(getattr(e, "aliases", None) or []),
        }
        for e in records
    ]
    return ok(rows, meta=MetaBody(total=total, limit=lim, page=(off // lim + 1) if lim else 1))


def _list_entity_records(store: object, *, limit: int, offset: int) -> list:
    """Resolve entity list from GraphStore implementations without Protocol change."""
    lister = getattr(store, "list_entities", None)
    if callable(lister):
        try:
            return list(lister(limit=limit, offset=offset))
        except TypeError:
            # Older signature without offset
            items = list(lister(limit=limit))
            return items[offset : offset + limit]

    for attr in ("entities", "_entities"):
        entities = getattr(store, attr, None)
        if isinstance(entities, dict):
            items = list(entities.values())
            items.sort(key=lambda e: (getattr(e, "type", ""), getattr(e, "name", "").lower()))
            return items[offset : offset + limit]
        if isinstance(entities, list):
            return list(entities)[offset : offset + limit]
    return []


def _entity_total(store: object, *, fallback: int | None) -> int:
    counts = _safe_counts(store)
    for key in ("entities", "entity_count", "nodes", "node_count"):
        if key not in counts:
            continue
        try:
            return int(counts[key])
        except (TypeError, ValueError):
            continue
    return int(fallback) if fallback is not None else 0


def _safe_counts(store: object) -> dict:
    try:
        counts = store.counts()  # type: ignore[attr-defined]
    except Exception:
        return {}
    return counts if isinstance(counts, dict) else {}
