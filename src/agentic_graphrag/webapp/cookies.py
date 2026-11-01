"""JWT access cookies and session-bound signed double-submit CSRF."""

from __future__ import annotations

import hmac
import time
from typing import Any

import jwt
from fastapi import Request, Response

from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.webapp.models import AuthSession, RefreshToken
from agentic_graphrag.webapp.runtime import WebRuntime
from agentic_graphrag.webapp.security import opaque_token, token_digest, unauthorized
from agentic_graphrag.webapp.sessions import SessionGrant

ACCESS = "agr_access"
REFRESH = "agr_refresh"
CSRF = "agr_csrf"
CSRF_HEADER = "X-CSRF-Token"


def cookie_name(ctx: WebRuntime, name: str) -> str:
    return f"__Host-{name}" if ctx.settings.secure_cookies else name


def read_cookie(ctx: WebRuntime, request: Request, name: str) -> str:
    return request.cookies.get(cookie_name(ctx, name), "")


def _set_cookie(
    ctx: WebRuntime, response: Response, *, name: str, value: str, seconds: int
) -> None:
    response.set_cookie(
        cookie_name(ctx, name),
        value,
        max_age=max(0, seconds),
        path="/",
        secure=ctx.settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


def decode_token(ctx: WebRuntime, token: str, *, audience: str) -> dict[str, Any]:
    try:
        value = jwt.decode(
            token,
            ctx.secret,
            algorithms=["HS256"],
            audience=audience,
            issuer="agr-webapp",
            options={"require": ["exp", "iat", "sid", "jti"]},
        )
    except jwt.InvalidTokenError as exc:
        raise unauthorized() from exc
    return value


def _jwt(ctx: WebRuntime, *, audience: str, seconds: int, claims: dict[str, Any]) -> str:
    return jwt.encode(
        {
            **claims,
            "iss": "agr-webapp",
            "aud": audience,
            "iat": int(time.time()),
            "exp": int(time.time()) + seconds,
            "jti": opaque_token(),
        },
        ctx.secret,
        algorithm="HS256",
    )


def set_csrf(ctx: WebRuntime, response: Response, *, session_id: str = "") -> str:
    value = _jwt(
        ctx, audience="agr-csrf", seconds=ctx.settings.refresh_seconds, claims={"sid": session_id}
    )
    _set_cookie(ctx, response, name=CSRF, value=value, seconds=ctx.settings.refresh_seconds)
    return value


def issue_session(ctx: WebRuntime, response: Response, grant: SessionGrant) -> str:
    token = _jwt(
        ctx,
        audience="agr-access",
        seconds=ctx.settings.access_seconds,
        claims={
            "sub": grant.user_id,
            "sid": grant.session_id,
            "tid": grant.tenant_id,
            "ver": grant.version,
        },
    )
    _set_cookie(ctx, response, name=ACCESS, value=token, seconds=ctx.settings.access_seconds)
    _set_cookie(
        ctx,
        response,
        name=REFRESH,
        value=grant.refresh_token,
        seconds=int(grant.expires_at - time.time()),
    )
    return set_csrf(ctx, response, session_id=grant.session_id)


def clear_session(ctx: WebRuntime, response: Response) -> None:
    for name in (ACCESS, REFRESH, CSRF):
        response.delete_cookie(
            cookie_name(ctx, name),
            path="/",
            secure=ctx.settings.secure_cookies,
            httponly=True,
            samesite="lax",
        )
    response.headers["Cache-Control"] = "no-store"


def bootstrap_csrf(ctx: WebRuntime, request: Request, response: Response) -> str:
    session_id = csrf_session(ctx, request)
    existing = read_cookie(ctx, request, CSRF)
    try:
        if decode_token(ctx, existing, audience="agr-csrf")["sid"] == session_id:
            return existing
    except ApiError:
        pass
    return set_csrf(ctx, response, session_id=session_id)


def csrf_session(ctx: WebRuntime, request: Request) -> str:
    raw = read_cookie(ctx, request, REFRESH)
    if not raw:
        return ""
    with ctx.database.transaction() as session:
        token = session.get(RefreshToken, token_digest(raw))
        if token is None or token.expires_at <= time.time():
            return ""
        record = session.get(AuthSession, token.session_id)
        if record is None or record.revoked_at is not None:
            return ""
        return record.id


def check_csrf(ctx: WebRuntime, request: Request) -> None:
    origin = request.headers.get("origin", "")
    token = request.headers.get(CSRF_HEADER, "")
    cookie = read_cookie(ctx, request, CSRF)
    valid = origin == ctx.settings.public_origin and bool(token and cookie)
    if not valid or not hmac.compare_digest(token, cookie):
        raise ApiError("CSRF_FAILED", "Refresh the page and try again", status_code=403)
    try:
        claims = decode_token(ctx, token, audience="agr-csrf")
        if claims["sid"] != csrf_session(ctx, request):
            raise unauthorized()
    except ApiError as exc:
        raise ApiError("CSRF_FAILED", "Refresh the page and try again", status_code=403) from exc
