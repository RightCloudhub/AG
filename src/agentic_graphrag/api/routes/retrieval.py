"""Stable retrieval-only, original-evidence and distinct search audit endpoints."""

from fastapi import APIRouter, Query, Request

from agentic_graphrag.api.envelope import TypedEnvelope, ok
from agentic_graphrag.api.routes.deps import knowledge_service, principal, service
from agentic_graphrag.app.errors import INVALID_INPUT, ApiError
from agentic_graphrag.app.evidence_sources import (
    catalog_visible,
    query_scope,
    source_store,
)
from agentic_graphrag.app.search_contracts import EvidenceResult, SearchRequest, SearchResult
from agentic_graphrag.app.search_service import search, withdraw_deleted_sources
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.knowledge import SourceRef

router = APIRouter(prefix="/v1", tags=["retrieval"])


@router.post("/search", response_model=TypedEnvelope[SearchResult], operation_id="search")
def post_search(body: SearchRequest, request: Request) -> JsonObject:
    tenant, user = principal(request)
    result = search(service(request), body, tenant_id=tenant, user_id=user)
    return ok(result.model_dump(mode="json"))


@router.get(
    "/evidence/{evidence_id}",
    response_model=TypedEnvelope[EvidenceResult],
    operation_id="get_evidence",
)
def get_evidence(
    evidence_id: str,
    request: Request,
    *,
    offset: int = Query(0, ge=0),
    limit: int = Query(4096, ge=1, le=65536),
) -> JsonObject:
    svc = knowledge_service(request)
    row = source_store(svc).get(query_scope(svc), evidence_id, kind="evidence")
    if row is None:
        raise ApiError(INVALID_INPUT, "Unknown evidence", status_code=404)
    refs = [SourceRef.model_validate(raw) for raw in row["source_refs"]]
    status = row["status"]
    text = str(row["content"])
    if not catalog_visible(svc, [row], current=False):
        status, text = "deleted", ""
    result = EvidenceResult(
        evidence_id=evidence_id,
        source_refs=refs,
        status=status,
        content=text[offset : offset + limit],
        offset=offset,
        total_chars=len(text),
    )
    return ok(result.model_dump(mode="json"))


@router.get(
    "/audit/searches/{search_id}",
    response_model=TypedEnvelope[SearchResult],
    operation_id="get_search_audit",
)
def get_search_audit(search_id: str, request: Request) -> JsonObject:
    svc = knowledge_service(request)
    row = source_store(svc).get(query_scope(svc), search_id, kind="search")
    if row is None:
        raise ApiError(INVALID_INPUT, "Unknown search audit", status_code=404)
    result = SearchResult.model_validate(row)
    withdraw_deleted_sources(svc, result)
    return ok(result.model_dump(mode="json"))
