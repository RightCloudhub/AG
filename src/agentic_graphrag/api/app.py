"""FastAPI application factory (P2-ARCH-03 + P3/P4 routes)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agentic_graphrag.api.auth import AuthRateLimitMiddleware
from agentic_graphrag.api.env_flags import env_flag
from agentic_graphrag.api.envelope import MetaBody, fail
from agentic_graphrag.api.errors import INTERNAL_ERROR, INVALID_INPUT, ApiError
from agentic_graphrag.api.routes import knowledge as knowledge_routes
from agentic_graphrag.api.routes import query as query_routes
from agentic_graphrag.api.service import QueryService, build_default_service
from agentic_graphrag.config import ROOT_DIR

_DEFAULT_API_HOST = "0.0.0.0"
_DEFAULT_API_PORT = 8000


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    svc: QueryService | None = getattr(app.state, "query_service", None)
    owns = False
    if svc is None:
        svc = build_default_service()
        app.state.query_service = svc
        owns = True
    try:
        yield
    finally:
        if owns and svc is not None:
            svc.close()


def create_app(*, query_service: QueryService | None = None) -> FastAPI:
    """Create the ASGI app. Pass ``query_service`` to inject a test double."""
    app = FastAPI(
        title="AgenticGraphRAG",
        version="0.2.0",
        description="Multi-hop agentic GraphRAG query API",
        lifespan=_lifespan,
    )
    if query_service is not None:
        app.state.query_service = query_service
    app.add_middleware(AuthRateLimitMiddleware)
    _register_exception_handlers(app)
    _register_routes(app)
    _mount_web_ui(app)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        body = fail(
            exc.code,
            exc.message,
            details=exc.details,
            meta=MetaBody(request_id=(exc.details or {}).get("request_id")),
        )
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        body = fail(
            INVALID_INPUT,
            "Request validation failed",
            details={"errors": _public_validation_errors(exc.errors())},
        )
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        del exc
        return JSONResponse(status_code=500, content=fail(INTERNAL_ERROR, "Internal server error"))


def _register_routes(app: FastAPI) -> None:
    @app.get("/healthz")
    def healthz(request: Request) -> dict[str, Any]:
        """Liveness + shallow dependency checks (graph/vector/doc store)."""
        return _health_payload(request)

    app.include_router(query_routes.router)
    app.include_router(knowledge_routes.router)


def _health_payload(request: Request) -> dict[str, Any]:
    svc = getattr(request.app.state, "query_service", None)
    if svc is None:
        return {"status": "degraded", "checks": {"query_service": "missing"}}
    checks: dict[str, str] = {"query_service": "ok"}
    overall = "ok"
    try:
        counts = svc.bundle.graph.counts()
        checks["graph"] = "ok"
        checks["graph_entities"] = str(counts.get("entities") or counts.get("nodes") or 0)
    except Exception as exc:  # noqa: BLE001
        checks["graph"] = f"error:{type(exc).__name__}"
        overall = "degraded"
    try:
        # Vector store may not expose counts; existence is enough for shallow check.
        _ = svc.bundle.vector
        checks["vector"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["vector"] = f"error:{type(exc).__name__}"
        overall = "degraded"
    try:
        ping = getattr(svc.bundle.graph, "ping", None)
        if callable(ping):
            ping()
            checks["graph_ping"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["graph_ping"] = f"error:{type(exc).__name__}"
        overall = "degraded"
    checks["allow_llm"] = "1" if svc.allow_llm else "0"
    checks["graph_backend"] = str(getattr(svc.bundle, "graph_backend", "unknown"))
    checks["vector_backend"] = str(getattr(svc.bundle, "vector_backend", "unknown"))
    return {"status": overall, "checks": checks}


def _mount_web_ui(app: FastAPI) -> None:
    web_dir = ROOT_DIR / "web"
    if not web_dir.is_dir():
        return
    static = web_dir / "static"
    if static.is_dir():
        app.mount("/web/static", StaticFiles(directory=str(static)), name="web-static")

    @app.get("/web", response_class=HTMLResponse)
    @app.get("/web/", response_class=HTMLResponse)
    def web_ui() -> FileResponse:
        index = web_dir / "index.html"
        if not index.exists():
            return HTMLResponse("<h1>Web UI missing</h1>", status_code=404)  # type: ignore[return-value]
        return FileResponse(index)


def _public_validation_errors(errors: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for err in errors:
        out.append(
            {
                "loc": list(err.get("loc", ())),
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
        )
    return out


def run_server() -> None:
    """Console entry ``agr-api`` — serve POST /v1/query."""
    import os

    import uvicorn

    host = os.environ.get("AGR_API_HOST", _DEFAULT_API_HOST)
    port = int(os.environ.get("AGR_API_PORT", str(_DEFAULT_API_PORT)))
    uvicorn.run(
        "agentic_graphrag.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=env_flag("AGR_API_RELOAD"),
    )
