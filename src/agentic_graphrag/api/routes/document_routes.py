"""Document deletion and resumable uploads; ownership always comes from auth."""

from __future__ import annotations

import mimetypes
from io import BytesIO
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from agentic_graphrag.api.envelope import ok
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.deps import principal, service
from agentic_graphrag.api.routes.document_mutations import ACCEPTED
from agentic_graphrag.api.routes.document_mutations import router as document_mutations_router
from agentic_graphrag.api.routes.document_read import router as document_read_router
from agentic_graphrag.api.routes.knowledge_upload import (
    emit_audit,
    link_source,
    parse_upload_content,
    retain_source,
    stage_files,
    validate_upload,
)
from agentic_graphrag.app.documents import index_changed, lifecycle_for
from agentic_graphrag.app.errors import INVALID_INPUT, ApiError
from agentic_graphrag.config import runtime_dir
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.ports.document_ops import SourceArtifact
from agentic_graphrag.core.ports.document_ops_store import DocumentOpsStore
from agentic_graphrag.core.ports.parsing import ParsedDocument
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.knowledge.document_commands import StagedFile
from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore
from agentic_graphrag.knowledge.resumable_upload import UploadSessions
from agentic_graphrag.observability.degradation import (
    REASON_UNEXPECTED,
    ChannelStatus,
    record_degradation,
)

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService

router = APIRouter(dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))])
router.include_router(document_read_router)
router.include_router(document_mutations_router)


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
        record_degradation(
            "document_lifecycle",
            REASON_UNEXPECTED,
            exc,
            severity=ChannelStatus.FAILED,
            doc_id=doc_id,
        )
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
        # Waiting on the session mutex must not stall every other request on the event loop.
        await run_in_threadpool(sessions.put, upload_id, tenant, number=number, body=bytes(buffer))
    except (KeyError, ValueError) as exc:
        raise _upload_error(exc) from exc
    return ok({"id": upload_id, "part": number, "status": "received"})


@router.post("/uploads/{upload_id}/complete", response_model=None)
def complete_upload(
    upload_id: str, request: Request, parse_async: bool = False
) -> JsonObject | JSONResponse:
    if parse_async:
        return _complete_async(upload_id, request)
    svc = service(request)
    sessions = _sessions(request)
    tenant, _ = principal(request)
    try:
        # Parsing can outlast any other request (MinerU: minutes), so the global session
        # mutex covers the reads and writes around it, never the parse itself.
        with sessions.mutex.hold():
            meta = sessions.get(upload_id, tenant)
            body = None if _completed(svc, meta, tenant) else sessions.content(upload_id, tenant)
        parsed = None if body is None else parse_upload_content(svc, meta["name"], body)
        with sessions.mutex.hold():
            meta = sessions.get(upload_id, tenant)
            if meta["result"]:
                return ok(meta["result"])
            result = _complete(svc, meta, parsed, tenant=tenant, content=body)
            sessions.finish(upload_id, tenant, result)
    except (KeyError, ValueError) as exc:
        raise _upload_error(exc) from exc
    emit_audit(request, {"action": "doc_upload", "target": upload_id, "outcome": "success"})
    return ok(result)


def _task_store(svc: QueryService) -> IngestTaskStore:
    if svc.ingest_tasks is None:
        raise ApiError("SERVICE_UNAVAILABLE", "Ingest queue unavailable", status_code=503)
    return svc.ingest_tasks


def _completed(svc: QueryService, meta: dict[str, Any], tenant: str) -> bool:
    """A finished session, or a crash between creating its task and recording it."""
    return bool(meta["result"]) or _task_store(svc).get(meta["id"], tenant_id=tenant) is not None


def _complete(
    svc: QueryService,
    meta: dict[str, Any],
    parsed: ParsedDocument | None,
    *,
    tenant: str,
    content: bytes | None = None,
) -> JsonObject:
    tasks = _task_store(svc)
    # The upload ID is the task ID, so completion can safely be retried after a crash.
    existing = tasks.get(meta["id"], tenant_id=tenant)
    if existing:
        return existing.to_dict()
    if parsed is None:
        raise ValueError("Upload state changed during completion; retry")
    # Retain the verified original before completion deletes its parts (K-02).
    sha = retain_source(svc, content, tenant_id=tenant) if content is not None else None
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
    if sha is not None and content is not None:
        artifact = SourceArtifact(sha, parsed.source_mime_type, meta["name"], len(content))
        link_source(svc, row, artifact, tenant_id=tenant)
    task = tasks.create(tenant, [row], task_id=meta["id"])
    index_changed(svc)
    return task.to_dict()


def _complete_async(upload_id: str, request: Request) -> JsonObject | JSONResponse:
    """``parse_async``: stage the verified original; parsing happens in a worker."""
    svc = service(request)
    sessions = _sessions(request)
    tenant, _ = principal(request)
    try:
        with sessions.mutex.hold():
            meta = sessions.get(upload_id, tenant)
            stored = _stored_completion(svc, meta, tenant)
            if stored is not None:
                return stored
            body = sessions.content(upload_id, tenant)
        media_type = mimetypes.guess_type(meta["name"])[0] or ""
        staged = StagedFile(meta["name"], body, meta["source_uri"], media_type)
        result = stage_files(svc, [staged], tenant_id=tenant, task_id=upload_id)
        with sessions.mutex.hold():
            if sessions.get(upload_id, tenant)["result"] is None:
                sessions.finish(upload_id, tenant, result)
    except (KeyError, ValueError) as exc:
        raise _upload_error(exc) from exc
    emit_audit(request, {"action": "doc_upload", "target": upload_id, "outcome": "accepted"})
    return JSONResponse(ok(result), status_code=ACCEPTED)


def _stored_completion(
    svc: QueryService, meta: dict[str, Any], tenant: str
) -> JsonObject | JSONResponse | None:
    """A repeated completion returns the original outcome and creates nothing."""
    result = meta["result"]
    if result:
        status = ACCEPTED if result.get("parse_async") else 200
        return JSONResponse(ok(result), status_code=status)
    existing = _task_store(svc).get(meta["id"], tenant_id=tenant)
    docs = svc.bundle.docs
    if existing is not None and not (
        isinstance(docs, DocumentOpsStore) and docs.get_op(meta["id"])
    ):
        return ok(existing.to_dict())
    return None
