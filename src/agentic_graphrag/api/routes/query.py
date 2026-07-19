"""POST /v1/query (FR-API-01)."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Request

from agentic_graphrag.api.envelope import MetaBody, ok
from agentic_graphrag.api.errors import ApiError
from agentic_graphrag.api.schemas import QueryRequest
from agentic_graphrag.api.service import QueryService

router = APIRouter(prefix="/v1", tags=["query"])


def _service(request: Request) -> QueryService:
    svc = getattr(request.app.state, "query_service", None)
    if svc is None:
        raise ApiError("SERVICE_UNAVAILABLE", "Query service not initialized", status_code=503)
    return svc


@router.post("/query")
def post_query(body: QueryRequest, request: Request) -> dict:
    request_id = request.headers.get("x-request-id") or str(uuid4())
    meta = MetaBody(request_id=request_id)
    try:
        result = _service(request).run_query(body)
        return ok(result.model_dump(mode="json"), meta=meta)
    except ApiError as exc:
        # re-raise so app-level handler sets status code
        exc.details = {**(exc.details or {}), "request_id": request_id}
        raise
