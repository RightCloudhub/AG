"""Knowledge management APIs (FR-API-03 / P3-KG-04 / ENT-04/06)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from pydantic import BaseModel, Field

from agentic_graphrag.api.envelope import MetaBody, ok
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.deps import knowledge_service as service
from agentic_graphrag.api.routes.deps import principal
from agentic_graphrag.api.routes.document_routes import router as document_router
from agentic_graphrag.api.routes.knowledge_upload import emit_audit, save_uploads, task_row
from agentic_graphrag.api.routes.workspace_tasks import get_cached_task, remember_task, task_kb
from agentic_graphrag.api.tenant import request_tenant
from agentic_graphrag.app.audit_reads import visible_query_audit
from agentic_graphrag.app.errors import INVALID_INPUT, ApiError
from agentic_graphrag.app.review_app import ReviewApp
from agentic_graphrag.core.enums import ReviewStatus
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.tenancy import tenant_matches
from agentic_graphrag.knowledge.review.queue import (
    ReviewAlreadyDecided,
    ReviewDecision,
    ReviewType,
)
from agentic_graphrag.knowledge.review.selection import InvalidReviewSelection

router = APIRouter(prefix="/v1", tags=["knowledge"])

router.include_router(document_router)

# Placeholder confidence for upload spot-checks until a real sampling policy
# exists — named so it reads as a policy value, not a magic number (BL-11).
UPLOAD_SPOTCHECK_CONFIDENCE = 0.5


class DocUploadMeta(BaseModel):
    title: str = ""
    source: str = ""
    doc_id: str | None = None


class ReviewDecisionBody(BaseModel):
    decision: str = Field(..., description="approve|reject|skip")
    reviewer: str = ""
    note: str = ""
    keep_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="Candidate entity ID to retain when approving an ambiguous open merge",
    )


class FeedbackBody(BaseModel):
    query_id: str
    accurate: bool
    reason: str = ""


@router.post("/docs", dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))])
async def upload_docs(
    request: Request,
    files: list[UploadFile] | None = None,
) -> JsonObject:
    """Batch document upload — persists content and creates an ingest task (FR-API-03)."""
    svc = service(request)
    tenant_id, _user_id = principal(request)
    task_id = str(uuid.uuid4())
    from agentic_graphrag.app.knowledge_writes import prepare_upload

    svc = prepare_upload(svc, task_id)
    saved = await save_uploads(svc, files or [], tenant_id=tenant_id, task_id=task_id)
    row = task_row(task_id, tenant_id, saved)
    if svc.ingest_tasks is not None:
        row = svc.ingest_tasks.create(tenant_id, saved, task_id=task_id).to_dict()
    row["kb_id"] = svc.knowledge_base.kb_id if svc.knowledge_base else "default"
    remember_task(task_id, row)
    review_docs = [r for r in saved if r.get("status") != "duplicate" and not r.get("error")]
    if svc.review_queue is not None and review_docs:
        svc.review_queue.enqueue(
            ReviewType.SPOTCHECK,
            {
                "task_id": task_id,
                "doc_count": len(saved),
                # Rejecting the spot-check rolls back facts sourced from these docs (BL-03).
                "doc_ids": [row["doc_id"] for row in review_docs if row.get("doc_id")],
            },
            confidence=UPLOAD_SPOTCHECK_CONFIDENCE,
            batch_id=task_id,
            tenant_id=tenant_id,
        )
    emit_audit(
        request,
        {"action": "doc_upload", "target": task_id, "outcome": "success", "doc_count": len(saved)},
    )
    return ok(row, meta=MetaBody(request_id=task_id))


@router.get(
    "/ingest-tasks/{task_id}",
    dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))],
)
def get_ingest_task(task_id: str, request: Request) -> JsonObject:
    tenant_id, _user_id = principal(request)
    svc = service(request)
    persisted = svc.ingest_tasks.get(task_id, tenant_id=tenant_id) if svc.ingest_tasks else None
    task = persisted.to_dict() if persisted is not None else get_cached_task(task_id)
    if task is not None and task_kb(task) != request.query_params.get("kb_id", "default"):
        task = None
    if task is None:
        raise ApiError(INVALID_INPUT, f"Unknown task: {task_id}", status_code=404)
    if not tenant_matches(task.get("tenant_id"), tenant_id):
        raise ApiError(INVALID_INPUT, f"Unknown task: {task_id}", status_code=404)
    return ok(task)


@dataclass
class ReviewQueueQuery:
    status: ReviewStatus | None = ReviewStatus.PENDING
    type: str | None = None
    limit: int = 50
    offset: int = 0


def _review_query(
    *,
    status: Annotated[ReviewStatus | Literal[""] | None, Query()] = ReviewStatus.PENDING,
    type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> ReviewQueueQuery:
    return ReviewQueueQuery(status=status or None, type=type, limit=limit, offset=offset)


@router.get(
    "/review-queue",
    dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))],
)
def list_review_queue(
    request: Request,
    q: Annotated[ReviewQueueQuery, Depends(_review_query)],
) -> JsonObject:
    svc = service(request)
    if svc.review_queue is None:
        return ok([], meta=MetaBody(total=0, limit=q.limit, page=1))
    tenant_id, _user_id = principal(request)
    pending_count = svc.review_queue.count(status=ReviewStatus.PENDING, tenant_id=tenant_id)
    items, total = svc.review_queue.list_page(
        status=q.status,
        type=q.type,
        limit=q.limit,
        offset=q.offset,
        tenant_id=tenant_id,
    )
    return ok(
        [i.to_dict() for i in items],
        meta=MetaBody(
            total=total,
            limit=q.limit,
            page=q.offset // q.limit + 1,
            extra={"pending_count": pending_count},
        ),
    )


@router.post(
    "/review-queue/{item_id}/decision",
    dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))],
)
def decide_review(item_id: str, body: ReviewDecisionBody, request: Request) -> JsonObject:
    svc = service(request)
    if svc.review_queue is None:
        raise ApiError("SERVICE_UNAVAILABLE", "Review queue not configured", status_code=503)
    tenant_id, actor = principal(request)
    try:
        dec = ReviewDecision(body.decision.lower())
    except ValueError as exc:
        raise ApiError(INVALID_INPUT, "decision must be approve|reject|skip") from exc
    try:
        # ReviewApp records the decision and applies it to the graph (BL-03).
        item = ReviewApp(svc).decide(
            item_id,
            dec,
            reviewer=actor,
            note=body.note,
            tenant_id=tenant_id,
            keep_id=body.keep_id,
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
    except InvalidReviewSelection as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=422) from exc
    emit_audit(
        request,
        {
            "action": "review_decision",
            "target": item_id,
            "outcome": dec.value,
            "reviewer": actor,
            "effect": item.execution.get("effect", "none"),
        },
    )
    return ok(item.to_dict())


@router.get("/audit/queries/{query_id}")
def get_audit_query(query_id: str, request: Request) -> JsonObject:
    """Reasoning chain audit lookup (FR-AN-04 / P3-AN-01) — tenant-scoped."""
    svc = service(request)
    if svc.audit_store is None:
        raise ApiError("SERVICE_UNAVAILABLE", "Audit store not configured", status_code=503)
    tenant_id, _user_id = principal(request)
    row = svc.audit_store.get_for_tenant(query_id, tenant_id)
    if row is None or (row.get("metadata") or {}).get(
        "kb_id", "default"
    ) != request.query_params.get("kb_id", "default"):
        # 404 for both missing and cross-tenant (no existence leak).
        raise ApiError(INVALID_INPUT, f"Unknown query_id: {query_id}", status_code=404)
    return ok(visible_query_audit(svc, row))


@router.post("/feedback")
def post_feedback(body: FeedbackBody, request: Request) -> JsonObject:
    """User accurate/inaccurate feedback (FR-OP-03 / P4-OPS-02)."""
    svc = service(request)
    tenant_id, user_id = principal(request)
    audit = svc.audit_store
    row = audit.get_for_tenant(body.query_id, tenant_id) if audit else None
    if audit is not None and (
        row is None
        or (row.get("metadata") or {}).get("kb_id", "default")
        != request.query_params.get("kb_id", "default")
    ):
        # 404 whether the id belongs to another tenant or does not exist at all.
        # Answering 404 only for the cross-tenant case made this an existence
        # probe (docs/design/BUSINESS_LOGIC.md BL-09).
        raise ApiError(INVALID_INPUT, f"Unknown query_id: {body.query_id}", status_code=404)
    result = svc.submit_feedback(
        body.query_id,
        accurate=body.accurate,
        reason=body.reason,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    emit_audit(
        request,
        {
            "action": "feedback_submitted",
            "target": body.query_id,
            "outcome": "accurate" if body.accurate else "inaccurate",
        },
    )
    return ok(result)


@router.get("/metrics", dependencies=[Depends(require_role(Role.ADMIN))])
def get_metrics_summary(request: Request) -> JsonObject:
    from agentic_graphrag.observability.metrics import get_metrics

    return ok(get_metrics().summary(tenant_id=request_tenant(request)))
