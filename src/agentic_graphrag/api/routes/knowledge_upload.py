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
# BL-08: pdf removed — no text extraction library is wired, so PDFs were
# stored as binary garbage and poisoned the chunk/index pipeline.
# Re-add when a PDF text extractor (pypdf / pdfplumber) is integrated.
ALLOWED_EXTENSIONS = frozenset({"md", "txt"})


def validate_upload(file: UploadFile) -> None:
    """Check a file extension against the upload allow list."""
    name = (file.filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    # BL-08: reject files without an extension (previously bypassed the whitelist).
    if not ext or ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise ApiError(
            INVALID_INPUT,
            f"File type not allowed: .{ext or '(none)'} (allowed: {allowed})",
            status_code=413,
        )


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
    # BL-08: check declared size before reading into memory to avoid OOM on huge uploads.
    declared = getattr(file, "size", None)
    if declared is not None and declared > MAX_FILE_SIZE_BYTES:
        raise ApiError(
            INVALID_INPUT,
            f"File too large: {file.filename} ({declared} bytes, max {MAX_FILE_SIZE_BYTES})",
            status_code=413,
        )
    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise ApiError(
            INVALID_INPUT,
            f"File too large: {file.filename} ({len(content)} bytes, max {MAX_FILE_SIZE_BYTES})",
            status_code=413,
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
    # BL-10: log persistence failures instead of silently swallowing.
    try:
        svc.bundle.docs.save(record)
    except Exception as exc:  # noqa: BLE001
        logger.error("DocStore.save failed for %s: %s", doc_id, exc)
        raise ApiError(
            "DOC_STORE_ERROR",
            "Failed to persist document",
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
    except Exception as exc:  # noqa: BLE001 — audit must not break the API operation
        logger.warning("emit_audit_event failed for %s: %s", event.get("action"), exc)
