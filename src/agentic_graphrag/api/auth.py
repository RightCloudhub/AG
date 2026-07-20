"""API key auth + rate limiting (NFR-06 / P4-UI-02).

Enable with env:
  AGR_API_KEYS=tenant1:key1,tenant2:key2
  AGR_REQUIRE_AUTH=1
  AGR_RATE_LIMIT_QPS=10
  AGR_RATE_LIMIT_CONCURRENT=5
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from agentic_graphrag.api.envelope import fail
from agentic_graphrag.api.errors import RATE_LIMITED


@dataclass
class Principal:
    tenant_id: str
    api_key: str
    user_id: str = "default"


def parse_api_keys(raw: str | None = None) -> dict[str, str]:
    """Map api_key → tenant_id from ``tenant:key`` pairs."""
    text = raw if raw is not None else os.environ.get("AGR_API_KEYS", "")
    out: dict[str, str] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            tenant, key = part.split(":", 1)
            out[key.strip()] = tenant.strip() or "default"
        else:
            out[part] = "default"
    return out


def require_auth_enabled() -> bool:
    return os.environ.get("AGR_REQUIRE_AUTH", "").lower() in {"1", "true", "yes"}


class RateLimiter:
    """Token-bucket-ish QPS + concurrent query limits per tenant."""

    def __init__(
        self,
        *,
        qps: float = 10.0,
        concurrent: int = 5,
        window_seconds: float = 1.0,
    ) -> None:
        self.qps = qps
        self.concurrent = concurrent
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._inflight: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def acquire(self, tenant_id: str) -> str | None:
        """Return error message if limited, else None and increment inflight."""
        now = time.time()
        with self._lock:
            hits = self._hits[tenant_id]
            while hits and now - hits[0] > self.window_seconds:
                hits.popleft()
            if len(hits) >= self.qps:
                return "rate limit: QPS exceeded"
            if self._inflight[tenant_id] >= self.concurrent:
                return "rate limit: concurrent queries exceeded"
            hits.append(now)
            self._inflight[tenant_id] += 1
            return None

    def release(self, tenant_id: str) -> None:
        with self._lock:
            self._inflight[tenant_id] = max(0, self._inflight[tenant_id] - 1)


def extract_api_key(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    """Optional auth + rate limit middleware."""

    def __init__(
        self,
        app: Callable,
        *,
        api_keys: dict[str, str] | None = None,
        require_auth: bool | None = None,
        rate_limiter: RateLimiter | None = None,
        public_paths: frozenset[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.api_keys = api_keys if api_keys is not None else parse_api_keys()
        self.require_auth = require_auth if require_auth is not None else require_auth_enabled()
        qps = float(os.environ.get("AGR_RATE_LIMIT_QPS", "20"))
        conc = int(os.environ.get("AGR_RATE_LIMIT_CONCURRENT", "10"))
        self.limiter = rate_limiter or RateLimiter(qps=qps, concurrent=conc)
        self.public_paths = public_paths or frozenset(
            {"/healthz", "/docs", "/openapi.json", "/redoc"}
        )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if path in self.public_paths or path.startswith("/web"):
            return await call_next(request)
        principal, err_resp = self._authenticate(request)
        if err_resp is not None:
            return err_resp
        request.state.principal = principal
        err = self.limiter.acquire(principal.tenant_id)
        if err:
            return JSONResponse(status_code=429, content=fail(RATE_LIMITED, err))
        try:
            return await call_next(request)
        finally:
            self.limiter.release(principal.tenant_id)

    def _authenticate(self, request: Request) -> tuple[Principal, Response | None]:
        key = extract_api_key(request)
        if self.require_auth:
            if not key or key not in self.api_keys:
                return (
                    Principal(tenant_id="default", api_key="", user_id="anonymous"),
                    JSONResponse(
                        status_code=401,
                        content=fail("UNAUTHORIZED", "Valid API key required"),
                    ),
                )
            return self._principal_for(key, request), None
        if key and key in self.api_keys:
            return self._principal_for(key, request), None
        return Principal(tenant_id="default", api_key="", user_id="anonymous"), None

    def _principal_for(self, key: str, request: Request) -> Principal:
        return Principal(
            tenant_id=self.api_keys[key],
            api_key=key,
            user_id=request.headers.get("x-user-id") or "default",
        )
