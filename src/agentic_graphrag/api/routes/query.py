"""POST /v1/query and /v1/query/stream (FR-API-01/02)."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from agentic_graphrag.api.envelope import MetaBody, TypedEnvelope, ok
from agentic_graphrag.api.routes.deps import principal, service
from agentic_graphrag.api.stream_bridge import StreamBridge
from agentic_graphrag.app.contracts import QueryRequest, QueryResultData
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.core.json_types import JsonObject

router = APIRouter(prefix="/v1", tags=["query"])


@router.post("/query", response_model=TypedEnvelope[QueryResultData], operation_id="query")
def post_query(body: QueryRequest, request: Request) -> JsonObject:
    request_id = request.headers.get("x-request-id") or str(uuid4())
    meta = MetaBody(request_id=request_id)
    tenant_id, user_id = principal(request)
    try:
        result = service(request).run_query(body, tenant_id=tenant_id, user_id=user_id)
        # Surface degraded channels on the envelope too (O-9): a caller that
        # ignores data.degraded_channels still sees the run was not clean.
        meta.degraded_channels = result.degraded_channels
        return ok(result.model_dump(mode="json"), meta=meta)
    except ApiError as exc:
        exc.details = {**(exc.details or {}), "request_id": request_id}
        raise


@router.post(
    "/query/stream",
    operation_id="stream_query",
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def post_query_stream(body: QueryRequest, request: Request) -> StreamingResponse:
    """SSE stream: triage → thinking → sub_question → hop_done → answer."""
    tenant_id, user_id = principal(request)
    svc = service(request)

    bridge = StreamBridge(
        lambda: svc.stream_query_events(body, tenant_id=tenant_id, user_id=user_id)
    )

    return StreamingResponse(
        bridge.frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Query-Id": bridge.query_id,
        },
    )
