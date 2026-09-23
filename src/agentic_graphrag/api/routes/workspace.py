"""Identity, ingest task, and graph browsing APIs used by the Web workspace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from agentic_graphrag.api.envelope import MetaBody, ok
from agentic_graphrag.api.errors import ApiError
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.knowledge_graph_browse import (
    DEFAULT_ENTITY_PAGE,
    MAX_ENTITY_PAGE,
    entity_page,
)
from agentic_graphrag.api.routes.workspace_tasks import list_cached_tasks
from agentic_graphrag.api.service import QueryService

router = APIRouter(prefix="/v1", tags=["workspace"])


@dataclass
class TaskListQuery:
    limit: int = 50
    offset: int = 0


def _task_list_query(
    *,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> TaskListQuery:
    return TaskListQuery(limit, offset)


@router.get(
    "/ingest-tasks",
    dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))],
)
def list_ingest_tasks(
    request: Request,
    page: Annotated[TaskListQuery, Depends(_task_list_query)],
) -> dict:
    """List recent ingest tasks for the current tenant."""
    tenant_id, _user_id = _principal(request)
    service = _service(request)
    if service.ingest_tasks is None:
        rows, total = list_cached_tasks(
            tenant_id=tenant_id, limit=page.limit, offset=page.offset
        )
    else:
        tasks, total = service.ingest_tasks.list(
            tenant_id=tenant_id, limit=page.limit, offset=page.offset
        )
        rows = [task.to_dict() for task in tasks]
    return ok(
        rows,
        meta=MetaBody(
            total=total,
            limit=page.limit,
            page=page.offset // page.limit + 1,
        ),
    )


@router.get("/me")
def get_current_user(request: Request) -> dict:
    """Return the authenticated identity and capabilities without role probing."""
    principal = getattr(request.state, "principal", None)
    role = principal.role.value if principal is not None else Role.READER.value
    capabilities = {
        "chat": True,
        "graph": True,
        "knowledge": role in {Role.ADMIN.value, Role.OPERATOR.value},
        "review": role in {Role.ADMIN.value, Role.OPERATOR.value},
        "ops": role == Role.ADMIN.value,
    }
    return ok(
        {
            "tenant_id": principal.tenant_id if principal is not None else "default",
            "user_id": principal.user_id if principal is not None else "anonymous",
            "role": role,
            "authenticated": bool(principal and principal.api_key),
            "capabilities": capabilities,
        }
    )


@dataclass
class GraphEntityQuery:
    limit: int = DEFAULT_ENTITY_PAGE
    offset: int = 0
    query: str = ""
    entity_type: str = ""


def _graph_entity_query(
    *,
    limit: int = Query(DEFAULT_ENTITY_PAGE, ge=1, le=MAX_ENTITY_PAGE),
    offset: int = Query(0, ge=0),
    query: str = Query("", max_length=200),
    entity_type: str = Query("", max_length=100),
) -> GraphEntityQuery:
    return GraphEntityQuery(limit, offset, query.strip(), entity_type.strip())


@router.get(
    "/graph/entities",
    dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR, Role.READER))],
)
def list_graph_entities(
    request: Request,
    page: Annotated[GraphEntityQuery, Depends(_graph_entity_query)],
) -> dict:
    """Return a tenant-scoped, searchable page of graph entities."""
    service = _service(request)
    tenant_id, _user_id = _principal(request)
    result = entity_page(
        service.bundle.graph,
        limit=page.limit,
        offset=page.offset,
        tenant_id=tenant_id,
        query=page.query,
        entity_type=page.entity_type,
    )
    rows = [
        {
            "id": entity.id,
            "name": entity.name,
            "type": entity.type,
            "aliases": list(getattr(entity, "aliases", None) or []),
        }
        for entity in result.records
    ]
    meta = MetaBody(
        total=result.total,
        limit=page.limit,
        page=page.offset // page.limit + 1,
        extra={"total_is_floor": True} if result.total_is_floor else {},
    )
    return ok(rows, meta=meta)


def _service(request: Request) -> QueryService:
    service = getattr(request.app.state, "query_service", None)
    if service is None:
        raise ApiError("SERVICE_UNAVAILABLE", "Query service not initialized", status_code=503)
    return service


def _principal(request: Request) -> tuple[str, str]:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        return "default", "anonymous"
    return principal.tenant_id, principal.user_id
