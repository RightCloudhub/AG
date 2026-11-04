"""Identity, ingest task, and graph browsing APIs used by the Web workspace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from agentic_graphrag.api.envelope import MetaBody, ok
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.deps import knowledge_service as service
from agentic_graphrag.api.routes.deps import principal
from agentic_graphrag.api.routes.knowledge_graph_browse import (
    DEFAULT_ENTITY_PAGE,
    MAX_ENTITY_PAGE,
    entity_page,
)
from agentic_graphrag.api.routes.workspace_tasks import list_cached_tasks
from agentic_graphrag.core.json_types import JsonObject

router = APIRouter(prefix="/v1", tags=["workspace"])
identity_router = APIRouter(prefix="/v1", tags=["workspace"])


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
) -> JsonObject:
    """List recent ingest tasks for the current tenant."""
    tenant_id, _user_id = principal(request)
    svc = service(request)
    if svc.ingest_tasks is None:
        rows, total = list_cached_tasks(
            tenant_id=tenant_id,
            limit=page.limit,
            offset=page.offset,
            kb_id=svc.knowledge_base.kb_id if svc.knowledge_base else "default",
        )
    else:
        tasks, total = svc.ingest_tasks.list(
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


@identity_router.get("/me")
def get_current_user(request: Request) -> JsonObject:
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
) -> JsonObject:
    """Return a tenant-scoped, searchable page of graph entities."""
    svc = service(request)
    tenant_id, _user_id = principal(request)
    from agentic_graphrag.app.query_selection import select_documents

    svc = select_documents(svc, None, tenant_id=tenant_id)
    result = entity_page(
        svc.bundle.graph,
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
            "aliases": list(entity.aliases),
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
