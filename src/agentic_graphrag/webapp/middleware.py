"""Web boundary authentication with CSRF and streaming-lifetime rate limits."""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agentic_graphrag.api.auth import RateLimiter, _bind_request_context, _clear_request_context
from agentic_graphrag.api.envelope import fail
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.webapp.authentication import authenticate, has_api_key
from agentic_graphrag.webapp.cookies import check_csrf
from agentic_graphrag.webapp.runtime import WebRuntime

_PUBLIC_AUTH = {"csrf", "register", "login", "logout", "refresh", "forgot", "reset"}


def _public(request: Request) -> bool:
    path = request.url.path.removeprefix("/v1")
    if path.startswith("/auth/") and path.removeprefix("/auth/") in _PUBLIC_AUTH:
        return True
    if request.method not in {"GET", "HEAD"}:
        return False
    if path in {"/", "/login", "/register", "/forgot", "/reset", "/healthz", "/web", "/web/"}:
        return True
    return path.startswith(("/app/", "/invite/", "/invites/", "/web/static/")) or path == "/app"


class WebAuthMiddleware:
    def __init__(self, app: ASGIApp, *, ctx: WebRuntime) -> None:
        self.app = app
        self.ctx = ctx
        self.limiter = RateLimiter(qps=20, concurrent=10)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        try:
            await run_in_threadpool(self._prepare, request)
        except ApiError as exc:
            response = JSONResponse(
                status_code=exc.status_code,
                content=fail(exc.code, exc.message, details=exc.details),
            )
            response.headers["Cache-Control"] = "no-store"
            await response(scope, receive, send)
            return
        current = getattr(request.state, "principal", None)
        if current:
            _bind_request_context(request, current)
        try:
            await self.app(scope, receive, _no_store(send))
        finally:
            if current:
                self.limiter.release(current.tenant_id)
                _clear_request_context()

    def _prepare(self, request: Request) -> None:
        request.state.enforce_auth = True
        keyed = has_api_key(request)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not keyed:
            check_csrf(self.ctx, request)
        try:
            current = authenticate(self.ctx, request)
        except ApiError:
            if keyed or not _public(request):
                raise
            return
        error = self.limiter.acquire(current.tenant_id)
        if error:
            raise ApiError("RATE_LIMITED", error, status_code=429)
        request.state.principal = current


def _no_store(send: Send) -> Send:
    async def sending(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = MutableHeaders(scope=message)
            headers["Cache-Control"] = "no-store"
            headers["X-Content-Type-Options"] = "nosniff"
            headers["Referrer-Policy"] = "same-origin"
        await send(message)

    return sending
