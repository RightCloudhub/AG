"""One-way adapter from the Web application to the existing GraphRAG API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response

from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.tenant import request_tenant
from agentic_graphrag.app.query_service import QueryService


def install_business(
    app: FastAPI,
    query_service: QueryService | None,
) -> AbstractAsyncContextManager[None]:
    from agentic_graphrag.api.routes import admin, install_business_routes

    install_business_routes(app)

    @app.get("/metrics-prom", dependencies=[Depends(require_role(Role.ADMIN))])
    def metrics(request: Request) -> Response:
        return Response(
            admin.prometheus_metrics_text(tenant_id=request_tenant(request)),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @asynccontextmanager
    async def lifecycle() -> AsyncIterator[None]:
        from agentic_graphrag.api.app import _validate_live_credentials
        from agentic_graphrag.app.query_service import build_default_service
        from agentic_graphrag.app.write_path import start_inline_worker

        service = query_service or build_default_service()
        _validate_live_credentials(service)
        app.state.query_service = service
        worker = start_inline_worker(service)
        try:
            yield
        finally:
            if worker:
                worker.stop()
            if query_service is None:
                service.close()

    if query_service is not None:
        app.state.query_service = query_service
    return lifecycle()
