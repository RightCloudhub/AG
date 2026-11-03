"""Document deletion and resumable UTF-8 uploads; ownership always comes from auth."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from fastapi import APIRouter, Depends, Request, UploadFile
from pydantic import BaseModel, Field

from agentic_graphrag.api.envelope import ok
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.deps import principal, service
from agentic_graphrag.api.routes.knowledge_upload import (
    emit_audit,
    parse_upload_content,
    validate_upload,
)
from agentic_graphrag.app.documents import index_changed, lifecycle_for
from agentic_graphrag.app.errors import INVALID_INPUT, ApiError
from agentic_graphrag.config import runtime_dir
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.knowledge.resumable_upload import UploadSessions

router = APIRouter(dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))])


class UploadInit(BaseModel):
    filename: str = Field(min_length=1, max_length=512)
    size: int = Field(gt=0)
    source_uri: str = Field(default="", max_length=2048)
    content_sha256: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")


def _sessions(request: Request) -> UploadSessions:
    cfg = service(request).cfg
    return UploadSessions(runtime_dir(cfg) / "uploads", cfg.indexing)


def _upload_error(exc: Exception) -> ApiError:
    if isinstance(exc, KeyError):
        return ApiError(INVALID_INPUT, "Unknown upload", status_code=404)
    return ApiError(INVALID_INPUT, str(exc), status_code=409)


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, request: Request) -> JsonObject:
    svc = service(request)
    tenant, _ = principal(request)
    try:
        lifecycle_for(svc).delete(doc_id, tenant_id=tenant)
    except KeyError as exc:
        raise ApiError(INVALID_INPUT, "Unknown document", status_code=404) from exc
    except Exception as exc:
        emit_audit(request, {"action": "doc_delete", "target": doc_id, "outcome": "partial"})
        raise ApiError(
            "SERVICE_UNAVAILABLE", "Delete pending reconciliation", status_code=503
        ) from exc
    finally:
        index_changed(svc)
    emit_audit(request, {"action": "doc_delete", "target": doc_id, "outcome": "success"})
    return ok({"doc_id": doc_id, "status": "deleted"})


@router.post("/uploads/init")
def init_upload(body: UploadInit, request: Request) -> JsonObject:
    validate_upload(UploadFile(filename=body.filename, file=BytesIO()), service(request))
    tenant, _ = principal(request)
    try:
        row = _sessions(request).init(
            tenant,
            name=body.filename,
            size=body.size,
            source_uri=body.source_uri,
            content_sha256=body.content_sha256,
        )
    except ValueError as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=413) from exc
    return ok(row)


@router.get("/uploads/{upload_id}")
def get_upload(upload_id: str, request: Request) -> JsonObject:
    tenant, _ = principal(request)
    try:
        return ok(_sessions(request).get(upload_id, tenant))
    except KeyError as exc:
        raise _upload_error(exc) from exc


@router.put("/uploads/{upload_id}/parts/{number}")
async def upload_part(upload_id: str, number: int, request: Request) -> JsonObject:
    sessions = _sessions(request)
    tenant, _ = principal(request)
    try:
        meta = sessions.get(upload_id, tenant)
        buffer = bytearray()
        async for chunk in request.stream():
            if len(buffer) + len(chunk) > meta["part_bytes"]:
                raise ApiError(INVALID_INPUT, "Part too large", status_code=413)
            buffer.extend(chunk)
        sessions.put(upload_id, tenant, number=number, body=bytes(buffer))
    except (KeyError, ValueError) as exc:
        raise _upload_error(exc) from exc
    return ok({"id": upload_id, "part": number, "status": "received"})


@router.post("/uploads/{upload_id}/complete")
def complete_upload(upload_id: str, request: Request) -> JsonObject:
    sessions = _sessions(request)
    tenant, _ = principal(request)
    try:
        with sessions.mutex.hold():
            meta = sessions.get(upload_id, tenant)
            if meta["result"]:
                return ok(meta["result"])
            result = _complete(request, sessions, meta, tenant=tenant)
            sessions.finish(upload_id, tenant, result)
    except (KeyError, ValueError) as exc:
        raise _upload_error(exc) from exc
    emit_audit(request, {"action": "doc_upload", "target": upload_id, "outcome": "success"})
    return ok(result)


def _complete(
    request: Request, sessions: UploadSessions, meta: dict[str, Any], *, tenant: str
) -> JsonObject:
    svc = service(request)
    if svc.ingest_tasks is None:
        raise ApiError("SERVICE_UNAVAILABLE", "Ingest queue unavailable", status_code=503)
    # The upload ID is the task ID, so completion can safely be retried after a crash.
    existing = svc.ingest_tasks.get(meta["id"], tenant_id=tenant)
    if existing:
        return existing.to_dict()
    body = sessions.content(meta["id"], tenant)
    parsed = parse_upload_content(svc, meta["name"], body)
    row = lifecycle_for(svc).accept(
        DocumentRecord(
            "",
            meta["name"],
            parsed.markdown,
            tenant_id=tenant,
            source_uri=meta["source_uri"],
            metadata={"task_id": meta["id"], "source": "upload", **parsed.metadata()},
        )
    )
    doc = svc.bundle.docs.get(row["doc_id"], tenant_id=tenant)
    if doc and doc.metadata.get("task_id") == meta["id"]:
        row["status"] = "queued"
    task = svc.ingest_tasks.create(tenant, [row], task_id=meta["id"])
    index_changed(svc)
    return task.to_dict()
