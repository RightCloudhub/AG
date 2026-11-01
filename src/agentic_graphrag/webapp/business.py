"""One-way adapter from the Web application to the existing GraphRAG API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

from agentic_graphrag.app.query_service import QueryService


def install_business(
    app: FastAPI,
    query_service: QueryService | None,
) -> AbstractAsyncContextManager[None]:
    from agentic_graphrag.api.routes import admin, knowledge, query, workspace

    for router in (query.router, knowledge.router, workspace.router, admin.router):
        app.include_router(router)

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
