"""Console support endpoints (P5-UI-02 M0): identity echo + task list.

``GET /v1/me`` — U-01: the UI must render navigation from the principal's
role instead of probing 403s (which would pollute the ENT-03 security audit
stream). Anonymous principals report the server-default role (reader), so the
role→view mapping is identical for auth-on and auth-off deployments.

``GET /v1/ingest-tasks`` — U-02: list view over the durable task store (the
single-task endpoint cannot render the knowledge-ops view's task table).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from agentic_graphrag.api.envelope import MetaBody, ok
from agentic_graphrag.api.errors import ApiError
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.knowledge import _service

router = APIRouter(prefix="/v1", tags=["console"])


def principal_payload(request: Request) -> dict[str, Any]:
    """{tenant_id, user_id, role} for the caller (anonymous ⇒ reader).

    Mirrors the middleware's anonymous principal so auth-on and auth-off
    deployments drive the same role→view mapping in the UI.
    """
    principal = getattr(request.state, "principal", None)
    if principal is None:
        return {"tenant_id": "default", "user_id": "anonymous", "role": "reader"}
    role = principal.role
    return {
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        "role": role.value if hasattr(role, "value") else str(role),
    }


@router.get("/me")
def get_me(request: Request) -> dict:
    """Identity echo for role-aware navigation (no permission probing)."""
    return ok(principal_payload(request))


@router.get("/ingest-tasks", dependencies=[Depends(require_role(Role.OPERATOR, Role.ADMIN))])
def list_ingest_tasks(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
) -> dict:
    """Task list for the knowledge-ops view (newest first, tenant-scoped)."""
    svc = _service(request)
    if svc.ingest_tasks is None:
        raise ApiError("SERVICE_UNAVAILABLE", "Ingest task store not configured", status_code=503)
    tenant_id = principal_payload(request)["tenant_id"]
    items = svc.ingest_tasks.list_tasks(limit=limit, offset=offset, tenant_id=tenant_id)
    total = svc.ingest_tasks.count_tasks(tenant_id=tenant_id)
    return ok(
        [t.to_dict() for t in items],
        meta=MetaBody(total=total, limit=limit, page=offset // max(limit, 1) + 1),
    )
