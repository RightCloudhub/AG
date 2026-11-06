"""Shared FastAPI dependencies for the /v1 route modules."""

from __future__ import annotations

from fastapi import Request

from agentic_graphrag.app.errors import SERVICE_UNAVAILABLE, ApiError
from agentic_graphrag.app.query_service import QueryService


def service(request: Request) -> QueryService:
    """The app's QueryService, or 503 when the process has none configured."""
    svc = getattr(request.app.state, "query_service", None)
    if not isinstance(svc, QueryService):
        raise ApiError(SERVICE_UNAVAILABLE, "Query service not initialized", status_code=503)
    return svc


def principal(request: Request) -> tuple[str, str]:
    """Tenant/user identity for budget + audit.

    Never trust client-supplied ``X-User-Id`` for budget identity: without an
    authenticated principal the request is anonymous in the default tenant.
    """
    p = getattr(request.state, "principal", None)
    if p is None:
        return "default", "anonymous"
    return p.tenant_id, p.user_id


def knowledge_service(request: Request) -> QueryService:
    from agentic_graphrag.app.knowledge_scope import select_service

    tenant_id, _ = principal(request)
    return select_service(
        service(request), tenant_id=tenant_id, kb_id=request.query_params.get("kb_id", "default")
    )
