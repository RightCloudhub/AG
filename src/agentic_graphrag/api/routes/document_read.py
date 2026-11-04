"""Document and chunk read endpoints for operators/admins (K-01).

Tenant scope always comes from the authenticated principal; IDs owned by another
tenant are indistinguishable from unknown IDs (404). Pages are bounded and sorted
deterministically; responses never include embeddings or server paths.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from agentic_graphrag.api.envelope import ok
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.deps import principal, service
from agentic_graphrag.api.routes.document_views import (
    Availability,
    chunk_detail,
    chunk_summary,
    document_summary,
)
from agentic_graphrag.app.errors import INVALID_INPUT, SERVICE_UNAVAILABLE, ApiError
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.ports.document_ops import DocumentQuery
from agentic_graphrag.core.ports.document_ops_store import DocumentOpsStore
from agentic_graphrag.stores.doc_projection import DOCUMENT_STATUSES

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService

DEFAULT_PAGE = 20
MAX_PAGE = 100
MAX_QUERY_CHARS = 200

router = APIRouter(dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))])


def ops_store(svc: QueryService) -> DocumentOpsStore:
    docs = svc.bundle.docs
    if not isinstance(docs, DocumentOpsStore):
        raise ApiError(
            SERVICE_UNAVAILABLE, "Document operations need a durable doc store", status_code=503
        )
    return docs


def availability(svc: QueryService) -> Availability:
    """Vector search is semantic only with a live embedder; offline hashing is not."""
    return Availability(vector=bool(svc.allow_llm), fulltext=True, graph=True)


def not_found(what: str) -> ApiError:
    return ApiError(INVALID_INPUT, f"Unknown {what}", status_code=404)


def _summaries(svc: QueryService, tenant: str, rows: list[dict[str, Any]]) -> list[JsonObject]:
    docs = ops_store(svc)
    ops = docs.doc_ops(tenant, [r["doc_id"] for r in rows])
    out: list[JsonObject] = []
    for row in rows:
        source = (
            None if row["reserved"] else docs.source_for((tenant, row["doc_id"], row["version"]))
        )
        summary = document_summary(
            row,
            ops=[op for op in ops if op.doc_id == row["doc_id"]],
            source=(source.sha256, source.media_type) if source else None,
            availability=availability(svc),
        )
        out.append(summary.model_dump(mode="json"))
    return out


def _document_query(
    request: Request,
    *,
    query: Annotated[str, Query(max_length=MAX_QUERY_CHARS)] = "",
    status: Annotated[str, Query(max_length=32)] = "",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentQuery:
    if status and status not in DOCUMENT_STATUSES:
        raise ApiError(
            INVALID_INPUT,
            f"status must be one of: {', '.join(DOCUMENT_STATUSES)}",
            status_code=422,
        )
    tenant, _ = principal(request)
    return DocumentQuery(tenant, query.strip(), status, limit, offset)


@router.get("/documents")
def list_documents(
    request: Request, q: Annotated[DocumentQuery, Depends(_document_query)]
) -> JsonObject:
    svc = service(request)
    rows, total = ops_store(svc).list_documents(q)
    return ok(
        {
            "items": _summaries(svc, q.tenant_id, rows),
            "total": total,
            "limit": q.limit,
            "offset": q.offset,
            "filters": {"query": q.query, "status": q.status},
        }
    )


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, request: Request) -> JsonObject:
    svc = service(request)
    tenant, _ = principal(request)
    row = ops_store(svc).document_row(tenant, doc_id)
    if row is None:
        raise not_found("document")
    return ok(_summaries(svc, tenant, [row])[0])


def _serving_document(svc: QueryService, tenant: str, doc_id: str) -> None:
    row = ops_store(svc).document_row(tenant, doc_id)
    if row is None or row["reserved"]:
        raise not_found("document")


@router.get("/documents/{doc_id}/chunks")
def list_chunks(
    doc_id: str,
    request: Request,
    *,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JsonObject:
    svc = service(request)
    tenant, _ = principal(request)
    _serving_document(svc, tenant, doc_id)
    rows, total = ops_store(svc).list_chunk_rows((tenant, doc_id), (limit, offset))
    items = [chunk_summary(row, availability(svc)).model_dump(mode="json") for row in rows]
    return ok({"items": items, "total": total, "limit": limit, "offset": offset})


@router.get("/documents/{doc_id}/chunks/{chunk_id}")
def get_chunk(doc_id: str, chunk_id: str, request: Request) -> JsonObject:
    svc = service(request)
    tenant, _ = principal(request)
    found = ops_store(svc).chunk_detail_row((tenant, doc_id, chunk_id))
    if found is None:
        raise not_found("chunk")
    row, payload = found
    return ok(chunk_detail(row, payload, availability(svc)).model_dump(mode="json"))
