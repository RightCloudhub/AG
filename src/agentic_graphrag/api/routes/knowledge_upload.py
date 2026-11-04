"""Document upload helpers for the knowledge API (ENT-03/06)."""

from __future__ import annotations

import mimetypes
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService

from fastapi import Request, UploadFile
from starlette.concurrency import run_in_threadpool

from agentic_graphrag.app.documents import index_changed, lifecycle_for
from agentic_graphrag.app.errors import INVALID_INPUT, SERVICE_UNAVAILABLE, ApiError
from agentic_graphrag.core.constants import BYTES_PER_MEBIBYTE
from agentic_graphrag.core.enums import IngestStatus
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.ports.document_ops import SourceArtifact
from agentic_graphrag.core.ports.document_ops_store import DocumentOpsStore
from agentic_graphrag.core.ports.parsing import (
    DocumentParseError,
    ParsedDocument,
    ParserServiceError,
    UnsupportedDocumentType,
)
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.knowledge.document_commands import StagedFile
from agentic_graphrag.knowledge.parsers import create_parser
from agentic_graphrag.knowledge.parsers.mineru import MINERU_REQUIRED_MESSAGE, mineru_available
from agentic_graphrag.knowledge.parsers.sniff import MINERU_MIMES, validate_filename
from agentic_graphrag.observability.degradation import (
    REASON_AUDIT_WRITE_FAILED,
    REASON_UNEXPECTED,
    ChannelStatus,
    record_degradation,
)

MAX_FILE_SIZE_BYTES = 50 * BYTES_PER_MEBIBYTE
MAX_BATCH_FILES = 100
READ_CHUNK_BYTES = 256 * 1024
UPLOAD_REJECT_STATUS = 413


def validate_upload(file: UploadFile, svc: QueryService) -> None:
    """Check the allow list before buffering; the parser also checks magic bytes.

    MinerU-only types fail here when MinerU is off, so neither a direct upload
    nor a resumable session transfers a body that can never be parsed.
    """
    try:
        extension = validate_filename(file.filename or "")
    except UnsupportedDocumentType as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=415) from exc
    if extension in MINERU_MIMES and not mineru_available(svc.cfg.knowledge.parsing.mineru):
        raise ApiError(INVALID_INPUT, MINERU_REQUIRED_MESSAGE, status_code=415)


async def save_uploads(
    svc: QueryService, files: list[UploadFile], *, tenant_id: str, task_id: str
) -> list[JsonObject]:
    # Reject before accepting anything: accepting can replace a document and clear its
    # indexes, while a failed request creates no task that would index the replacement.
    _check_batch(svc, files)
    prepared = [
        await _prepare_upload(svc, file, tenant_id=tenant_id, task_id=task_id) for file in files
    ]
    saved: list[dict[str, str]] = [await _accept_upload(svc, item) for item in prepared]
    return saved


async def stage_uploads(
    svc: QueryService, files: list[UploadFile], *, tenant_id: str, task_id: str
) -> JsonObject:
    """``parse_async``: validate and durably stage originals; a worker parses them."""
    _check_batch(svc, files)
    staged = [
        StagedFile(
            name=file.filename or "",
            data=await _read_capped(file, svc.cfg.indexing.max_file_bytes),
            source_uri=f"upload://{file.filename}",
            media_type=mimetypes.guess_type(file.filename or "")[0] or "",
        )
        for file in files
    ]
    if not staged:
        raise ApiError(INVALID_INPUT, "No documents received", status_code=422)
    return await run_in_threadpool(stage_files, svc, staged, tenant_id=tenant_id, task_id=task_id)


def stage_files(
    svc: QueryService, staged: list[StagedFile], *, tenant_id: str, task_id: str
) -> JsonObject:
    from agentic_graphrag.api.routes.document_mutations import commands_for

    op = commands_for(svc).stage_async_parse(tenant_id, staged, task_id=task_id)
    task = svc.ingest_tasks.get(op.task_id, tenant_id=tenant_id) if svc.ingest_tasks else None
    row: JsonObject = (
        task.to_dict() if task else {"id": op.task_id, "status": IngestStatus.QUEUED.value}
    )
    row["docs"] = op.task_rows()
    row["parse_async"] = True
    return row


def retain_source(svc: QueryService, content: bytes, *, tenant_id: str) -> str | None:
    """Store immutable original bytes before acceptance; ``None`` when unsupported."""
    docs = svc.bundle.docs
    if not isinstance(docs, DocumentOpsStore):
        return None
    try:
        return docs.artifacts.put(tenant_id, content)
    except OSError as exc:
        record_degradation(
            "source_artifacts", REASON_UNEXPECTED, exc, severity=ChannelStatus.FAILED
        )
        return None


def link_source(
    svc: QueryService, row: dict[str, str], artifact: SourceArtifact | None, *, tenant_id: str
) -> None:
    """Attach a retained original to the version this upload created."""
    docs = svc.bundle.docs
    if artifact is None or row.get("status") != "queued" or not row.get("doc_id"):
        return
    if isinstance(docs, DocumentOpsStore):
        docs.record_source((tenant_id, row["doc_id"], int(row["version"])), artifact)


def _check_batch(svc: QueryService, files: list[UploadFile]) -> None:
    max_files = svc.cfg.indexing.max_batch_files
    if len(files) > max_files:
        raise ApiError(
            INVALID_INPUT,
            f"Too many files: max {max_files}",
            status_code=UPLOAD_REJECT_STATUS,
        )
    for file in files:
        validate_upload(file, svc)


async def _read_capped(file: UploadFile, limit: int = MAX_FILE_SIZE_BYTES) -> bytes:
    """Read the upload in slices, rejecting as soon as it exceeds the cap.

    ``await file.read()`` materialized the whole upload before the size check,
    so the limit could not stop an oversized body (BL-08). Note this caps what
    the application buffers; capping the *request body* itself belongs to the
    server/proxy layer.
    """
    buffer = bytearray()
    while True:
        chunk = await file.read(READ_CHUNK_BYTES)
        if not chunk:
            return bytes(buffer)
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise ApiError(
                INVALID_INPUT,
                f"File too large: {file.filename} (max {limit} bytes)",
                status_code=UPLOAD_REJECT_STATUS,
            )


async def _parse_upload(svc: QueryService, file: UploadFile, content: bytes) -> ParsedDocument:
    return await run_in_threadpool(parse_upload_content, svc, file.filename or "", content)


def parse_upload_content(svc: QueryService, filename: str, content: bytes) -> ParsedDocument:
    """Parse with the configured adapter, mapping parser failures to API errors."""
    parser = create_parser(svc.cfg.knowledge.parsing)
    try:
        return parser.parse(content, filename=filename)
    except UnsupportedDocumentType as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=415) from exc
    except ParserServiceError as exc:
        raise ApiError(SERVICE_UNAVAILABLE, str(exc), status_code=503) from exc
    except DocumentParseError as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=422) from exc


async def _prepare_upload(
    svc: QueryService, file: UploadFile, *, tenant_id: str, task_id: str
) -> tuple[DocumentRecord, bytes]:
    content = await _read_capped(file, svc.cfg.indexing.max_file_bytes)
    parsed = await _parse_upload(svc, file, content)
    return DocumentRecord(
        doc_id="",
        title=file.filename or "",
        content=parsed.markdown,
        metadata={
            "task_id": task_id,
            "source": "upload",
            "bytes": len(content),
            "tenant_id": tenant_id,
            **parsed.metadata(),
        },
        tenant_id=tenant_id,
        source_uri=f"upload://{file.filename}",
    ), content


async def _accept_upload(
    svc: QueryService, prepared: tuple[DocumentRecord, bytes]
) -> dict[str, str]:
    record, content = prepared
    row = {"doc_id": "", "bytes": str(record.metadata["bytes"]), "name": record.title}
    try:
        sha = await run_in_threadpool(retain_source, svc, content, tenant_id=record.tenant_id)
        row = await run_in_threadpool(lifecycle_for(svc).accept, record)
        artifact = _artifact(record, sha, size=len(content))
        await run_in_threadpool(link_source, svc, row, artifact, tenant_id=record.tenant_id)
        await run_in_threadpool(index_changed, svc)
    except Exception as exc:  # noqa: BLE001 — one bad doc must not fail the batch
        # Previously a bare ``pass``: the doc silently never landed while the
        # task still listed it as queued (docs/design/BUSINESS_LOGIC.md BL-10).
        # ``accept`` writes the doc store and clears indexes, so either can be the cause.
        record_degradation(
            "document_lifecycle",
            REASON_UNEXPECTED,
            exc,
            severity=ChannelStatus.FAILED,
            upload_name=record.title,
        )
        row["error"] = f"save_failed: {type(exc).__name__}"
    return row


def _artifact(record: DocumentRecord, sha: str | None, *, size: int) -> SourceArtifact | None:
    if sha is None:
        return None
    media_type = str(record.metadata.get("source_mime_type") or "")
    return SourceArtifact(sha, media_type, record.title, size)


def task_row(task_id: str, tenant_id: str, saved: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "id": task_id,
        "tenant_id": tenant_id,
        "status": IngestStatus.QUEUED if saved else IngestStatus.EMPTY,
        "docs": saved,
        "created_at": time.time(),
        "message": (
            "Documents persisted to doc store; run extract pipeline offline or via worker"
            if saved
            else "No documents received"
        ),
    }


def emit_audit(request: Request, event: dict[str, Any]) -> None:
    """Best-effort audit emission; audit storage must not break the API operation."""
    try:
        from agentic_graphrag.governance.audit_events import emit_audit_event

        principal = getattr(request.state, "principal", None)
        tenant_id = principal.tenant_id if principal else "default"
        user_id = principal.user_id if principal else "anonymous"
        emit_audit_event(tenant_id=tenant_id, user_id=user_id, **event)
    except Exception as exc:  # noqa: BLE001
        record_degradation(
            "audit_events", REASON_AUDIT_WRITE_FAILED, exc, severity=ChannelStatus.FAILED
        )
