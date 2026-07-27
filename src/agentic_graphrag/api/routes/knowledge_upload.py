"""Document upload helpers for the knowledge API (ENT-03/06)."""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import Request, UploadFile

from agentic_graphrag.api.errors import INVALID_INPUT, ApiError
from agentic_graphrag.stores.interfaces import DocumentRecord

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
MAX_BATCH_FILES = 20
ALLOWED_EXTENSIONS = frozenset({"md", "txt", "pdf"})


def validate_upload(file: UploadFile) -> None:
    """Check a file extension against the upload allow list.

    Fixes BL-08: files without an extension must be rejected (not silently
    passed through), and size is checked *before* the file is read into memory.
    """
    name = (file.filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if not ext:
        raise ApiError(
            INVALID_INPUT,
            f"File has no extension and cannot be uploaded: {file.filename or '<unnamed>'}",
            status_code=400,
        )
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise ApiError(
            INVALID_INPUT,
            f"File type not allowed: .{ext} (allowed: {allowed})",
            status_code=400,
        )
    # Check file size *before* reading to avoid loading large blobs into memory.
    # UploadFile.seek() only accepts offset (no whence); use the underlying file object.
    try:
        raw = getattr(file, "file", file)
        raw.seek(0, 2)  # seek to end
        size = raw.tell()
        raw.seek(0)  # rewind for reading
        if size > MAX_FILE_SIZE_BYTES:
            raise ApiError(
                INVALID_INPUT,
                f"File too large: {file.filename} ({size} bytes, max {MAX_FILE_SIZE_BYTES})",
                status_code=413,
            )
    except (OSError, ValueError, AttributeError):
        pass  # seek failed; proceed and check after read (belt-and-suspenders)


async def save_uploads(
    svc: Any, files: list[UploadFile], *, tenant_id: str, task_id: str
) -> list[dict]:
    if len(files) > MAX_BATCH_FILES:
        raise ApiError(INVALID_INPUT, f"Too many files: max {MAX_BATCH_FILES}", status_code=413)
    saved: list[dict[str, str]] = []
    for file in files:
        saved.append(await _save_upload(svc, file, tenant_id=tenant_id, task_id=task_id))
    return saved


async def _save_upload(
    svc: Any, file: UploadFile, *, tenant_id: str, task_id: str
) -> dict[str, str]:
    validate_upload(file)
    # Re-extract extension (validate_upload doesn't return it) for PDF handling.
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise ApiError(
            INVALID_INPUT,
            f"File too large: {file.filename} ({len(content)} bytes, max {MAX_FILE_SIZE_BYTES})",
            status_code=413,
        )
    # BL-08: PDFs are not parsed as text — reject them with a clear message.
    if ext == "pdf":
        raise ApiError(
            INVALID_INPUT,
            "PDF upload is not yet supported; please convert to .txt or .md first.",
            status_code=415,
        )
    text = content.decode("utf-8", errors="replace")
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
    # BL-10: propagate save failures instead of silently swallowing them.
    try:
        svc.bundle.docs.save(record)
    except Exception as exc:  # noqa: BLE001 — surface to caller
        raise ApiError(
            INVALID_INPUT,
            f"Failed to persist document {doc_id}: {exc}",
            status_code=500,
        ) from exc
    return {"doc_id": doc_id, "bytes": str(len(text)), "name": file.filename or ""}


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
