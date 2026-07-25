"""API key auth + rate limiting (NFR-06 / P4-UI-02 / ENT-04 RBAC).

Enable with env:
  AGR_API_KEYS=tenant1:key1:role,tenant2:key2  (role: admin|operator|reader; default reader)
  AGR_REQUIRE_AUTH=1
  AGR_RATE_LIMIT_QPS=10
  AGR_RATE_LIMIT_CONCURRENT=5
  AGR_TRUST_X_USER_ID=1   # optional; default off — X-User-Id is not budget identity
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from agentic_graphrag.api.envelope import fail
from agentic_graphrag.api.errors import RATE_LIMITED
from agentic_graphrag.api.rbac import KeyInfo, Role, parse_api_keys_with_roles
from agentic_graphrag.observability.otel_bridge import extract_otel_context

_USER_ID_DIGEST_LEN = 16


@dataclass
class Principal:
    tenant_id: str
    api_key: str
    user_id: str = "default"
    role: Role = field(default=Role.READER)


def parse_api_keys(raw: str | None = None) -> dict[str, str]:
    """Map api_key → tenant_id from ``tenant:key[:role]`` pairs.

    Backward-compatible wrapper around :func:`parse_api_keys_with_roles`.
    """
    from agentic_graphrag.api.rbac import keys_to_tenant_map

    return keys_to_tenant_map(parse_api_keys_with_roles(raw))


def require_auth_enabled() -> bool:
    return os.environ.get("AGR_REQUIRE_AUTH", "").lower() in {"1", "true", "yes"}


def trust_x_user_id_enabled() -> bool:
    """When false (default), client X-User-Id cannot mint new user budget buckets."""
    return os.environ.get("AGR_TRUST_X_USER_ID", "").lower() in {"1", "true", "yes"}


def user_id_for_api_key(api_key: str) -> str:
    """Stable, non-spoofable user budget key derived from the API key."""
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:_USER_ID_DIGEST_LEN]
    return f"key:{digest}"


class RateLimiter:
    """Token-bucket-ish QPS + concurrent query limits per tenant."""

    def __init__(
        self,
        *,
        qps: float = 10.0,
        concurrent: int = 5,
        window_seconds: float = 1.0,
        tenant_overrides: dict[str, tuple[float, int]] | None = None,
    ) -> None:
        self.qps = qps
        self.concurrent = concurrent
        self.window_seconds = window_seconds
        self.tenant_overrides = tenant_overrides or {}
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._inflight: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def acquire(self, tenant_id: str) -> str | None:
        """Return error message if limited, else None and increment inflight."""
        now = time.time()
        t_qps, t_conc = self.tenant_overrides.get(tenant_id, (self.qps, self.concurrent))
        with self._lock:
            hits = self._hits[tenant_id]
            while hits and now - hits[0] > self.window_seconds:
                hits.popleft()
            qps_limit = max(1, int(t_qps))
            if len(hits) >= qps_limit:
                return "rate limit: QPS exceeded"
            if self._inflight[tenant_id] >= t_conc:
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
    """Optional auth + rate limit middleware (with RBAC + audit events)."""

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
        self._key_infos = parse_api_keys_with_roles()
        self.api_keys = api_keys if api_keys is not None else _tenant_map(self._key_infos)
        self.require_auth = require_auth if require_auth is not None else require_auth_enabled()
        qps = float(os.environ.get("AGR_RATE_LIMIT_QPS", "20"))
        conc = int(os.environ.get("AGR_RATE_LIMIT_CONCURRENT", "10"))
        overrides = _tenant_rate_overrides()
        self.limiter = rate_limiter or RateLimiter(
            qps=qps, concurrent=conc, tenant_overrides=overrides
        )
        self.public_paths = public_paths or frozenset(
            {"/healthz", "/docs", "/openapi.json", "/redoc", "/metrics-prom"}
        )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        with extract_otel_context(dict(request.headers)):
            return await self._dispatch_authenticated(request, call_next)

    async def _dispatch_authenticated(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if path in self.public_paths or path.startswith("/web"):
            return await call_next(request)
        principal, err_resp = self._authenticate(request)
        if err_resp is not None:
            _emit_auth_failure(request, principal.tenant_id)
            return err_resp
        request.state.principal = principal
        _bind_request_context(request, principal)
        err = self.limiter.acquire(principal.tenant_id)
        if err:
            _emit_rate_limit(principal)
            return JSONResponse(status_code=429, content=fail(RATE_LIMITED, err))
        try:
            return await call_next(request)
        finally:
            self.limiter.release(principal.tenant_id)
            _clear_request_context()

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
        if trust_x_user_id_enabled():
            raw = (request.headers.get("x-user-id") or "").strip()
            user_id = raw or "default"
        else:
            user_id = user_id_for_api_key(key)
        key_info = self._key_infos.get(key)
        role = key_info.role if key_info else Role.READER
        return Principal(
            tenant_id=self.api_keys[key],
            api_key=key,
            user_id=user_id,
            role=role,
        )


def _tenant_map(keys: dict[str, KeyInfo]) -> dict[str, str]:
    return {k: info.tenant_id for k, info in keys.items()}


def _tenant_rate_overrides() -> dict[str, tuple[float, int]]:
    from agentic_graphrag.config import get_config

    overrides: dict[str, tuple[float, int]] = {}
    for tenant_id, config in get_config().tenants.items():
        if config.qps is None and config.concurrent is None:
            continue
        overrides[tenant_id] = (
            config.qps if config.qps is not None else 20.0,
            config.concurrent if config.concurrent is not None else 10,
        )
    return overrides


def _bind_request_context(request: Request, principal: Principal) -> None:
    """Inject logging context vars for the current request."""
    try:
        from agentic_graphrag.observability.logging_setup import bind_context

        rid = request.headers.get("x-request-id") or ""
        bind_context(request_id=rid, tenant_id=principal.tenant_id, user_id=principal.user_id)
    except Exception:  # noqa: BLE001
        pass


def _clear_request_context() -> None:
    try:
        from agentic_graphrag.observability.logging_setup import clear_context

        clear_context()
    except Exception:  # noqa: BLE001
        pass


def _emit_auth_failure(request: Request, tenant_id: str) -> None:
    try:
        from agentic_graphrag.observability.audit_events import AUTH_FAILURE, emit_audit_event

        emit_audit_event(
            AUTH_FAILURE,
            tenant_id=tenant_id,
            target=request.url.path,
            outcome="denied",
        )
    except Exception:  # noqa: BLE001
        pass


def _emit_rate_limit(principal: Principal) -> None:
    try:
        from agentic_graphrag.observability.audit_events import RATE_LIMITED as RL_EVENT
        from agentic_graphrag.observability.audit_events import emit_audit_event

        emit_audit_event(
            RL_EVENT,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            outcome="denied",
        )
    except Exception:  # noqa: BLE001
        pass
