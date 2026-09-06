"""Knowledge management APIs (FR-API-03 / P3-KG-04 / ENT-04/06)."""

from __future__ import annotations

import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from pydantic import BaseModel, Field

from agentic_graphrag.api.envelope import MetaBody, ok
from agentic_graphrag.api.errors import INVALID_INPUT, ApiError
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.knowledge_graph_browse import (
    DEFAULT_ENTITY_PAGE,
    MAX_ENTITY_PAGE,
    entity_page,
)
from agentic_graphrag.api.routes.knowledge_upload import emit_audit, save_uploads, task_row
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.knowledge.review.executor import ReviewExecutor
from agentic_graphrag.knowledge.review.queue import (
    ReviewAlreadyDecided,
    ReviewDecision,
    ReviewType,
)

router = APIRouter(prefix="/v1", tags=["knowledge"])

# In-process compatibility index; durable state uses QueryService.ingest_tasks.
# Bounded: it is a fallback cache, not the source of truth, so old rows are
# evicted rather than accumulating for the process lifetime (BL-11).
_TASKS: OrderedDict[str, dict[str, Any]] = OrderedDict()
MAX_CACHED_TASKS = 500
# Placeholder confidence for upload spot-checks until a real sampling policy
# exists — named so it reads as a policy value, not a magic number (BL-11).
UPLOAD_SPOTCHECK_CONFIDENCE = 0.5


def _remember_task(task_id: str, row: dict[str, Any]) -> None:
    _TASKS[task_id] = row
    _TASKS.move_to_end(task_id)
    while len(_TASKS) > MAX_CACHED_TASKS:
        _TASKS.popitem(last=False)


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


@router.post("/docs", dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))])
async def upload_docs(
    request: Request,
    files: list[UploadFile] | None = None,
) -> dict:
    """Batch document upload — persists content and creates an ingest task (FR-API-03)."""
    svc = _service(request)
    tenant_id, _user_id = _principal(request)
    task_id = str(uuid.uuid4())
    saved = await save_uploads(svc, files or [], tenant_id=tenant_id, task_id=task_id)
    row = task_row(task_id, tenant_id, saved)
    if svc.ingest_tasks is not None:
        row = svc.ingest_tasks.create(tenant_id, saved, task_id=task_id).to_dict()
    _remember_task(task_id, row)
    if svc.review_queue is not None and saved:
        svc.review_queue.enqueue(
            ReviewType.SPOTCHECK,
            {"task_id": task_id, "doc_count": len(saved)},
            confidence=UPLOAD_SPOTCHECK_CONFIDENCE,
            batch_id=task_id,
            tenant_id=tenant_id,
        )
    emit_audit(
        request,
        {"action": "doc_upload", "target": task_id, "outcome": "success", "doc_count": len(saved)},
    )
    return ok(row, meta=MetaBody(request_id=task_id))


@router.get("/ingest-tasks/{task_id}")
def get_ingest_task(task_id: str, request: Request) -> dict:
    tenant_id, _user_id = _principal(request)
    svc = _service(request)
    persisted = svc.ingest_tasks.get(task_id, tenant_id=tenant_id) if svc.ingest_tasks else None
    task = persisted.to_dict() if persisted is not None else _TASKS.get(task_id)
    if task is None:
        raise ApiError(INVALID_INPUT, f"Unknown task: {task_id}", status_code=404)
    if task.get("tenant_id") not in {None, tenant_id}:
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
    tenant_id, _user_id = _principal(request)
    items = svc.review_queue.list(
        status=q.status,
        type=q.type,
        limit=q.limit,
        offset=q.offset,
        tenant_id=tenant_id,
    )
    return ok(
        [i.to_dict() for i in items],
        meta=MetaBody(total=len(items), limit=q.limit, page=q.offset // max(q.limit, 1) + 1),
    )


@router.post(
    "/review-queue/{item_id}/decision",
    dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))],
)
def decide_review(item_id: str, body: ReviewDecisionBody, request: Request) -> dict:
    svc = _service(request)
    if svc.review_queue is None:
        raise ApiError("SERVICE_UNAVAILABLE", "Review queue not configured", status_code=503)
    tenant_id, _user_id = _principal(request)
    try:
        dec = ReviewDecision(body.decision.lower())
    except ValueError as exc:
        raise ApiError(INVALID_INPUT, "decision must be approve|reject|skip") from exc
    try:
        item = svc.review_queue.decide(
            item_id,
            dec,
            reviewer=body.reviewer,
            note=body.note,
            tenant_id=tenant_id,
        )
    except KeyError as exc:
        # Same 404 for unknown and cross-tenant ids — no existence probe (BL-09).
        raise ApiError(INVALID_INPUT, f"Unknown review item: {item_id}", status_code=404) from exc
    except ReviewAlreadyDecided as exc:
        raise ApiError(
            INVALID_INPUT,
            f"Review item already decided: {exc.item.status}",
            status_code=409,
        ) from exc
    emit_audit(
        request,
        {
            "action": "review_decision",
            "target": item_id,
            "outcome": dec.value,
            "reviewer": body.reviewer,
        },
    )
    # BL-03: a recorded decision is not enough — approvals and rejections must
    # produce the graph side effects the item implies. Executor failures do not
    # undo the decision; they are reported so ops can re-apply consciously.
    try:
        graph_effects = ReviewExecutor(svc.bundle.graph).apply(item, dec.value)
    except Exception as exc:  # noqa: BLE001
        graph_effects = {"applied": False, "reason": f"executor error: {type(exc).__name__}"}
    payload = {**item.to_dict(), "graph_effects": graph_effects}
    return ok(payload)


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
    audit = svc.audit_store
    if audit is not None and audit.get_for_tenant(body.query_id, tenant_id) is None:
        # 404 whether the id belongs to another tenant or does not exist at all.
        # Answering 404 only for the cross-tenant case made this an existence
        # probe (docs/BUSINESS_LOGIC.md BL-09).
        raise ApiError(INVALID_INPUT, f"Unknown query_id: {body.query_id}", status_code=404)
    result = svc.submit_feedback(
        body.query_id,
        accurate=body.accurate,
        reason=body.reason,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    return ok(result)


@router.get("/metrics", dependencies=[Depends(require_role(Role.ADMIN))])
def get_metrics_summary() -> dict:
    from agentic_graphrag.observability.metrics import get_metrics

    return ok(get_metrics().summary())


@router.get(
    "/graph/entities",
    dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR, Role.READER))],
)
def list_graph_entities(request: Request, limit: int = 50, offset: int = 0) -> dict:
    """Minimal graph browse API scaffold (P5-CAP-01).

    Supports stores that expose ``list_entities`` (InMemoryGraphStore) or a
    public/private entity map (``entities`` / ``_entities``). Tenant-scoped:
    the store already accepts ``tenant_id`` — this route simply never passed it
    (docs/BUSINESS_LOGIC.md BL-09). ``meta.total`` counts the tenant's rows, not
    the whole graph, and is marked as a floor when the scan cap is reached.
    """
    svc = _service(request)
    store = svc.bundle.graph
    tenant_id, _user_id = _principal(request)
    lim = max(0, min(int(limit or DEFAULT_ENTITY_PAGE), MAX_ENTITY_PAGE))
    off = max(0, int(offset or 0))

    page = entity_page(store, limit=lim, offset=off, tenant_id=tenant_id)
    rows = [
        {
            "id": e.id,
            "name": e.name,
            "type": e.type,
            "aliases": list(getattr(e, "aliases", None) or []),
        }
        for e in page.records
    ]
    meta = MetaBody(
        total=page.total,
        limit=lim,
        page=(off // lim + 1) if lim else 1,
        extra={"total_is_floor": True} if page.total_is_floor else {},
    )
    return ok(rows, meta=meta)
