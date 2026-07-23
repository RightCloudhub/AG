"""POST /v1/query and /v1/query/stream (FR-API-01/02)."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from agentic_graphrag.api.envelope import MetaBody, ok
from agentic_graphrag.api.errors import ApiError
from agentic_graphrag.api.schemas import QueryRequest
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.api.sse import format_sse

router = APIRouter(prefix="/v1", tags=["query"])


def _service(request: Request) -> QueryService:
    svc = getattr(request.app.state, "query_service", None)
    if svc is None:
        raise ApiError("SERVICE_UNAVAILABLE", "Query service not initialized", status_code=503)
    return svc


def _principal(request: Request) -> tuple[str, str]:
    p = getattr(request.state, "principal", None)
    if p is None:
        # Never trust client-supplied X-User-Id for budget identity.
        return "default", "anonymous"
    return p.tenant_id, p.user_id


@router.post("/query")
def post_query(body: QueryRequest, request: Request) -> dict:
    request_id = request.headers.get("x-request-id") or str(uuid4())
    meta = MetaBody(request_id=request_id)
    tenant_id, user_id = _principal(request)
    try:
        result = _service(request).run_query(body, tenant_id=tenant_id, user_id=user_id)
        return ok(result.model_dump(mode="json"), meta=meta)
    except ApiError as exc:
        exc.details = {**(exc.details or {}), "request_id": request_id}
        raise


@router.post("/query/stream")
def post_query_stream(body: QueryRequest, request: Request) -> StreamingResponse:
    """SSE stream: triage → sub_question → hop_done → answer (P3-PERF-06)."""
    tenant_id, user_id = _principal(request)
    svc = _service(request)

    def gen():
        for etype, payload in svc.stream_query_events(body, tenant_id=tenant_id, user_id=user_id):
            yield format_sse(etype, payload)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
