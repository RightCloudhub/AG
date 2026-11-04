"""Chunk edit/visibility and reparse commands (K-03/K-04), returning 202 + durable work.

A 202 means the saved state and its recoverable pending work are both durable in
the doc store; the ingest task is dispatched idempotently (immediately, or by the
next worker poll after a crash). ``index_ready`` is always false here: saved text
and index-ready text are distinct states, reported per stage on the chunk.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.api.envelope import ok
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.deps import principal, service
from agentic_graphrag.api.routes.document_read import availability, ops_store
from agentic_graphrag.api.routes.document_views import chunk_summary
from agentic_graphrag.api.routes.knowledge_upload import emit_audit
from agentic_graphrag.app.documents import index_changed
from agentic_graphrag.app.errors import INVALID_INPUT, SERVICE_UNAVAILABLE, ApiError
from agentic_graphrag.core.ports.document_ops import (
    ChunkPatch,
    CommandOutcome,
    DocOperation,
    DocumentOpsError,
)
from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.knowledge.document_commands import DocumentCommands, InvalidCommand
from agentic_graphrag.stores.doc_projection import record_projection

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService

ACCEPTED = 202
MAX_KEY_CHARS = 200
MAX_CHUNK_TEXT_CHARS = 200_000

router = APIRouter(dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))])
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


class ChunkPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, max_length=MAX_CHUNK_TEXT_CHARS)
    enabled: bool | None = None
    expected_revision: int = Field(ge=1)


class ReparseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    mode: Literal["source", "rechunk"] = "source"


def commands_for(svc: QueryService) -> DocumentCommands:
    if svc.ingest_tasks is None:
        raise ApiError(SERVICE_UNAVAILABLE, "Ingest queue unavailable", status_code=503)
    knowledge = svc.cfg.knowledge
    return DocumentCommands(
        ops_store(svc),
        svc.ingest_tasks,
        tokenizer=knowledge.tokenizer,
        max_tokens=knowledge.chunk_size_tokens,
    )


def _key(value: str | None) -> str:
    key = (value or "").strip()
    if not key or len(key) > MAX_KEY_CHARS:
        raise ApiError(
            INVALID_INPUT,
            f"Idempotency-Key header is required (1-{MAX_KEY_CHARS} characters)",
            status_code=400,
        )
    return key


def _api_error(exc: Exception) -> ApiError:
    if isinstance(exc, DocumentOpsError):
        return ApiError(exc.code, exc.message, status_code=exc.status_code, details=exc.details)
    return ApiError(INVALID_INPUT, str(exc), status_code=422)


def _accepted(request: Request, outcome: CommandOutcome, audit: dict[str, Any]) -> JSONResponse:
    if not outcome.replayed:
        index_changed(service(request))
        emit_audit(request, {**audit, "outcome": "accepted"})
    return JSONResponse(ok(outcome.response), status_code=ACCEPTED)


@router.patch("/documents/{doc_id}/chunks/{chunk_id}")
def patch_chunk(
    doc_id: str,
    chunk_id: str,
    *,
    body: ChunkPatchBody,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    key = _key(idempotency_key)
    if body.text is None and body.enabled is None:
        raise ApiError(INVALID_INPUT, "Provide text and/or enabled", status_code=422)
    svc = service(request)
    tenant, _ = principal(request)
    view = availability(svc)

    def respond(chunk: ChunkRecord, op: DocOperation) -> dict[str, Any]:
        summary = chunk_summary(record_projection(chunk), view).model_dump(mode="json")
        return {
            "chunk": summary,
            "saved_revision": chunk.revision,
            "task_id": op.task_id,
            "index_ready": False,
        }

    patch = ChunkPatch(tenant, doc_id, chunk_id, body.expected_revision, body.text, body.enabled)
    try:
        outcome = commands_for(svc).patch_chunk(patch, key=key, respond=respond)
    except (DocumentOpsError, InvalidCommand) as exc:
        raise _api_error(exc) from exc
    audit = {"action": "chunk_update", "target": f"{doc_id}/{chunk_id}"}
    return _accepted(request, outcome, audit)


@router.post("/documents/{doc_id}/reparse")
def reparse_document(
    doc_id: str,
    body: ReparseBody,
    request: Request,
    *,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    key = _key(idempotency_key)
    svc = service(request)
    tenant, _ = principal(request)

    def respond(op: DocOperation) -> dict[str, Any]:
        return {
            "doc_id": doc_id,
            "task_id": op.task_id,
            "expected_version": body.expected_version,
            "mode": body.mode,
            "state": "queued",
        }

    try:
        outcome = commands_for(svc).request_reparse(
            (tenant, doc_id),
            expected_version=body.expected_version,
            mode=body.mode,
            key=key,
            respond=respond,
        )
    except (DocumentOpsError, InvalidCommand) as exc:
        raise _api_error(exc) from exc
    return _accepted(request, outcome, {"action": "doc_reparse", "target": doc_id})


def document_capabilities(svc: object, *, operator: bool) -> dict[str, bool]:
    """Capability flags for ``GET /v1/me``; false unless the backend supports them."""
    from agentic_graphrag.app.query_service import QueryService
    from agentic_graphrag.core.ports.document_ops_store import DocumentOpsStore

    durable = isinstance(svc, QueryService) and isinstance(svc.bundle.docs, DocumentOpsStore)
    queued = durable and isinstance(svc, QueryService) and svc.ingest_tasks is not None
    return {
        "documents": operator and durable,
        "chunk_mutations": operator and queued,
        "document_reparse": operator and queued,
        "parse_async": operator and queued,
    }
