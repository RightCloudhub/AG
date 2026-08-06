"""FastAPI application factory (P2-ARCH-03 + P3/P4 routes + ENT enterprise)."""

from __future__ import annotations

import logging os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from agentic_graphrag.api.auth import AuthRateLimitMiddleware
from agentic_graphrag.api.envelope import MetaBody, fail
from agentic_graphrag.api.errors import INTERNAL_ERROR, INVALID_INPUT, ApiError
from agentic_graphrag.api.routes import admin as admin_routes
from agentic_graphrag.api.routes import graph_browse as graph_browse_routes
from agentic_graphrag.api.routes import knowledge as knowledge_routes
from agentic_graphrag.api.routes import query as query_routes
from agentic_graphrag.api.service import QueryService, build_default_service
from agentic_graphrag.config import ROOT_DIR
from agentic_graphrag.observability.logging_setup import get_logger, setup_logging

_log = get_logger("api.app")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    import os

    setup_logging()
    if os.environ.get("AGR_OTEL_ENABLED", "").lower() in {"1", "true"}:
        from agentic_graphrag.observability.otel_bridge import setup_otel

        sample_rate = float(os.environ.get("AGR_OTEL_SAMPLE_RATE", "0.1"))
        setup_otel(endpoint=os.environ.get("AGR_OTEL_ENDPOINT"), sample_rate=sample_rate)
    _log.info("Application starting")
    svc: QueryService | None = getattr(app.state, "query_service", None)
    owns = False
    if svc is None:
        svc = build_default_service()
        app.state.query_service = svc
        owns = True
    _validate_live_credentials(svc)
    # BL-01: opt-in background ingest worker. When ``AGR_INGEST_WORKER=1`` is
    # set, the API process consumes the upload task queue and (when LLM is
    # also enabled) extracts triples into the knowledge graph — closing the
    # runtime write-path gap between ``POST /v1/docs`` and the graph.
    if _env_flag("AGR_INGEST_WORKER"):
        svc.start_background_workers()
        _log.info(
            "Ingest worker started (graph_write=%s)",
            "on" if (svc.ingest_worker and svc.ingest_worker.graph_write_enabled) else "off",
        )
    try:
        yield
    finally:
        if owns and svc is not None:
            svc.close()
        _log.info("Application shutting down")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


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
        _log.warning("ApiError %s: %s", exc.code, exc.message)
        body = fail(
            exc.code,
            exc.message,
            details=exc.details,
            meta=MetaBody(request_id=(exc.details or {}).get("request_id")),
        )
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        _log.warning("Validation error: %s", exc.errors())
        body = fail(
            INVALID_INPUT,
            "Request validation failed",
            details={"errors": _public_validation_errors(exc.errors())},
        )
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        _log.error("Unhandled exception: %s", type(exc).__name__, exc_info=exc)
        return JSONResponse(status_code=500, content=fail(INTERNAL_ERROR, "Internal server error"))


def _validate_live_credentials(svc: QueryService) -> None:
    """Fail fast on obvious misconfiguration when live modes are enabled (ENT-06b)."""
    import os

    errors: list[str] = []
    truthy = {"1", "true", "yes"}
    if os.environ.get("AGR_USE_LIVE_STORES", "").lower() in truthy:
        missing = _missing_store_credentials(svc)
        if missing:
            errors.append("AGR_USE_LIVE_STORES=1 missing " + ", ".join(missing))
    if os.environ.get("AGR_ALLOW_LLM", "").lower() in truthy and not _valid_llm_key(svc):
        errors.append("AGR_ALLOW_LLM=1 but LLM_API_KEY is missing or placeholder")
    if errors:
        raise RuntimeError("Startup credential validation failed: " + "; ".join(errors))


def _missing_store_credentials(svc: QueryService) -> list[str]:
    required = {
        "NEO4J_URI": svc.settings.neo4j_uri,
        "NEO4J_USER": svc.settings.neo4j_user,
        "NEO4J_PASSWORD": svc.settings.neo4j_password,
        "QDRANT_URL": svc.settings.qdrant_url,
        "QDRANT_COLLECTION": svc.settings.qdrant_collection,
    }
    return [name for name, value in required.items() if not str(value or "").strip()]


def _valid_llm_key(svc: QueryService) -> bool:
    key = svc.settings.llm_api_key
    return bool(key and key.strip() and "your-key" not in key)


def _register_routes(app: FastAPI) -> None:
    @app.get("/healthz")
    def healthz(request: Request) -> dict[str, Any]:
        """Liveness + shallow dependency checks (graph/vector/doc store)."""
        return _health_payload(request)

    @app.get("/metrics-prom", response_class=PlainTextResponse)
    def metrics_prom() -> PlainTextResponse:
        """Prometheus text exposition (ENT-08)."""
        from agentic_graphrag.api.routes.admin import prometheus_metrics_text

        return PlainTextResponse(
            content=prometheus_metrics_text(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    app.include_router(query_routes.router)
    # admin first so the static /audit/queries/recent wins over /{query_id}.
    app.include_router(admin_routes.router)
    app.include_router(knowledge_routes.router)
    app.include_router(graph_browse_routes.router)


def _health_payload(request: Request) -> dict[str, Any]:
    svc = getattr(request.app.state, "query_service", None)
    if svc is None:
        return {"status": "degraded", "checks": {"query_service": "missing"}}
    checks: dict[str, str] = {"query_service": "ok"}
    degraded = not _check_graph(svc, checks)
    _check_vector(svc, checks)
    degraded = not _check_graph_ping(svc, checks) or degraded
    checks["allow_llm"] = "1" if svc.allow_llm else "0"
    checks["graph_backend"] = str(getattr(svc.bundle, "graph_backend", "unknown"))
    checks["vector_backend"] = str(getattr(svc.bundle, "vector_backend", "unknown"))
    _add_operational_checks(svc, checks)
    return {"status": "degraded" if degraded else "ok", "checks": checks}


def _check_graph(svc: QueryService, checks: dict[str, str]) -> bool:
    try:
        counts = svc.bundle.graph.counts()
        checks["graph"] = "ok"
        checks["graph_entities"] = str(counts.get("entities") or counts.get("nodes") or 0)
        return True
    except Exception as exc:  # noqa: BLE001
        checks["graph"] = f"error:{type(exc).__name__}"
        return False


def _check_vector(svc: QueryService, checks: dict[str, str]) -> None:
    try:
        _ = svc.bundle.vector
        checks["vector"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["vector"] = f"error:{type(exc).__name__}"


def _check_graph_ping(svc: QueryService, checks: dict[str, str]) -> bool:
    try:
        ping = getattr(svc.bundle.graph, "ping", None)
        if callable(ping):
            ping()
            checks["graph_ping"] = "ok"
        return True
    except Exception as exc:  # noqa: BLE001
        checks["graph_ping"] = f"error:{type(exc).__name__}"
        return False


def _add_operational_checks(svc: QueryService, checks: dict[str, str]) -> None:
    try:
        from agentic_graphrag.llm.circuit import CircuitBreaker

        circuit = getattr(svc, "_circuit_breaker", None)
        if isinstance(circuit, CircuitBreaker):
            checks["llm_circuit"] = circuit.state.value
    except Exception:  # noqa: BLE001
        pass
    if svc.review_queue is None:
        return
    try:
        checks["review_queue_pending"] = str(svc.review_queue.counts().get("pending", 0))
    except Exception:  # noqa: BLE001
        pass


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


class _DropInvalidHttpWarnings(logging.Filter):
    """Silence uvicorn's 'Invalid HTTP request received' noise.

    Triggered by port scanners or TLS-to-plain-HTTP clients; harmless to
    the server but floods logs when bound to a public interface.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "Invalid HTTP request received" not in record.getMessage()


def run_server() -> None:
    """Console entry ``agr-api`` — serve POST /v1/query."""
    import os

    import uvicorn

    logging.getLogger("uvicorn.error").addFilter(_DropInvalidHttpWarnings())

    host = os.environ.get("AGR_API_HOST", "0.0.0.0")
    port = int(os.environ.get("AGR_API_PORT", "8000"))
    uvicorn.run(
        "agentic_graphrag.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=os.environ.get("AGR_API_RELOAD", "").lower() in {"1", "true", "yes"},
    )
