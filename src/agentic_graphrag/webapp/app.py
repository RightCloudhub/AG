"""Independent Web factory. Identity-only tests do not load the reasoning engine."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from typing import TYPE_CHECKING

from fastapi import FastAPI

from agentic_graphrag.webapp.database import Database
from agentic_graphrag.webapp.errors import register_error_handlers
from agentic_graphrag.webapp.frontend import install_frontend
from agentic_graphrag.webapp.logging import protect_access_logs
from agentic_graphrag.webapp.mail import MailSender, create_mail_sender
from agentic_graphrag.webapp.middleware import WebAuthMiddleware
from agentic_graphrag.webapp.routes_account import router as account_router
from agentic_graphrag.webapp.routes_auth import router as auth_router
from agentic_graphrag.webapp.routes_invites import router as invite_router
from agentic_graphrag.webapp.routes_keys import router as key_router
from agentic_graphrag.webapp.routes_tenants import router as tenant_router
from agentic_graphrag.webapp.runtime import WebRuntime
from agentic_graphrag.webapp.settings import WebSettings

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def create_app(
    *,
    settings: WebSettings | None = None,
    database: Database | None = None,
    mail: MailSender | None = None,
    include_business: bool = True,
    query_service: QueryService | None = None,
) -> FastAPI:
    config = settings or WebSettings()
    db = database or Database(config.database_url)
    if config.auto_migrate:
        db.upgrade()
    ctx = WebRuntime(
        database=db,
        settings=config,
        secret=config.signing_secret(),
        mail=mail or create_mail_sender(config),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        protect_access_logs()
        try:
            async with business:
                yield
        finally:
            if database is None:
                db.close()

    app = FastAPI(title="AGR Web", version="0.1.0", lifespan=lifespan)
    app.state.identity = ctx
    app.add_middleware(WebAuthMiddleware, ctx=ctx)
    register_error_handlers(app)
    for prefix in ("/v1", ""):
        app.include_router(auth_router, prefix=prefix)
        app.include_router(account_router, prefix=prefix)
        app.include_router(tenant_router, prefix=prefix)
        app.include_router(invite_router, prefix=prefix)
        app.include_router(key_router, prefix=prefix)
    business: AbstractAsyncContextManager[None] = nullcontext()
    if include_business:
        from agentic_graphrag.webapp.business import install_business

        business = install_business(app, query_service)

    @app.get("/healthz")
    def health() -> dict[str, object]:
        return {"status": "ok", "checks": {"identity": "ok"}}

    install_frontend(app)
    return app


def run_server() -> None:
    import uvicorn

    uvicorn.run("agentic_graphrag.webapp.app:create_app", factory=True, host="127.0.0.1", port=8000)
