"""Document upload helpers for the knowledge API (ENT-03/06)."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import Request, UploadFile

from agentic_graphrag.api.errors import INVALID_INPUT, ApiError
from agentic_graphrag.stores.interfaces import DocumentRecord

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
MAX_BATCH_FILES = 20
READ_CHUNK_BYTES = 256 * 1024
# Text-only: every upload is decoded as UTF-8 and chunked as plain text. ``pdf``
# used to sit here while no extractor existed, so PDFs were stored as mojibake
# and indexed as if they were text (docs/BUSINESS_LOGIC.md BL-08).
ALLOWED_EXTENSIONS = frozenset({"md", "txt"})
# Rejected with an explicit "not supported yet" instead of a generic type error.
KNOWN_UNSUPPORTED_EXTENSIONS = frozenset({"pdf"})
UPLOAD_REJECT_STATUS = 413


def validate_upload(file: UploadFile) -> None:
    """Check a file extension against the upload allow list.

    Extension-less names used to short-circuit the check entirely, so a file
    simply named ``payload`` bypassed the allow list (BL-08).
    """
    name = (file.filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext in ALLOWED_EXTENSIONS:
        return
    if ext in KNOWN_UNSUPPORTED_EXTENSIONS:
        raise ApiError(
            INVALID_INPUT,
            f".{ext} upload is not supported yet (no text extractor); "
            f"convert to {' or '.join(sorted(ALLOWED_EXTENSIONS))} first",
            status_code=UPLOAD_REJECT_STATUS,
        )
    allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
    shown = f".{ext}" if ext else "(no extension)"
    raise ApiError(
        INVALID_INPUT,
        f"File type not allowed: {shown} (allowed: {allowed})",
        status_code=UPLOAD_REJECT_STATUS,
    )


async def save_uploads(
    svc: Any, files: list[UploadFile], *, tenant_id: str, task_id: str
) -> list[dict]:
    if len(files) > MAX_BATCH_FILES:
        raise ApiError(
            INVALID_INPUT,
            f"Too many files: max {MAX_BATCH_FILES}",
            status_code=UPLOAD_REJECT_STATUS,
        )
    saved: list[dict[str, str]] = []
    for file in files:
        saved.append(await _save_upload(svc, file, tenant_id=tenant_id, task_id=task_id))
    return saved


async def _read_capped(file: UploadFile) -> bytes:
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
        if len(buffer) > MAX_FILE_SIZE_BYTES:
            raise ApiError(
                INVALID_INPUT,
                f"File too large: {file.filename} (max {MAX_FILE_SIZE_BYTES} bytes)",
                status_code=UPLOAD_REJECT_STATUS,
            )


def _decode_text(file: UploadFile, content: bytes) -> str:
    """Strict UTF-8 decode — never index replacement-character mojibake (BL-08)."""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApiError(
            INVALID_INPUT,
            f"File is not valid UTF-8 text: {file.filename}",
            status_code=UPLOAD_REJECT_STATUS,
        ) from exc


async def _save_upload(
    svc: Any, file: UploadFile, *, tenant_id: str, task_id: str
) -> dict[str, str]:
    validate_upload(file)
    content = await _read_capped(file)
    text = _decode_text(file, content)
    doc_id = file.filename or str(uuid.uuid4())
    record = DocumentRecord(
        doc_id=doc_id,
        title=file.filename or doc_id,
        content=text,
        metadata={
            "task_id": task_id,
            "source": "upload",
            "bytes": len(text),
            "tenant_id": tenant_id,
        },
        tenant_id=tenant_id,
    )
    row = {"doc_id": doc_id, "bytes": str(len(text)), "name": file.filename or ""}
    try:
        svc.bundle.docs.save(record)
    except Exception as exc:  # noqa: BLE001 — one bad doc must not fail the batch
        # Previously a bare ``pass``: the doc silently never landed while the
        # task still listed it as queued (docs/BUSINESS_LOGIC.md BL-10).
        logger.error("Doc store save failed for %s: %s", doc_id, exc, exc_info=True)
        row["error"] = f"save_failed: {type(exc).__name__}"
    return row


def task_row(task_id: str, tenant_id: str, saved: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "id": task_id,
        "tenant_id": tenant_id,
        "status": "queued" if saved else "empty",
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
        from agentic_graphrag.observability.audit_events import emit_audit_event

        principal = getattr(request.state, "principal", None)
        tenant_id = principal.tenant_id if principal else "default"
        user_id = principal.user_id if principal else "anonymous"
        emit_audit_event(tenant_id=tenant_id, user_id=user_id, **event)
    except Exception:  # noqa: BLE001
        pass
