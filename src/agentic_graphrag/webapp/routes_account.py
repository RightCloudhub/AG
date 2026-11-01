"""Current identity, profile, password and device session management."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request, Response

from agentic_graphrag.api.envelope import ok
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.webapp import accounts, cookies, sessions
from agentic_graphrag.webapp.identity import identity
from agentic_graphrag.webapp.models import AuthSession, User
from agentic_graphrag.webapp.routes_auth import _limit
from agentic_graphrag.webapp.runtime import principal, runtime, user_principal
from agentic_graphrag.webapp.schemas import PasswordInput, ProfileInput
from agentic_graphrag.webapp.security import field_error, unauthorized

router = APIRouter(tags=["account"])


@router.get("/me")
def me(request: Request) -> dict[str, Any]:
    return ok(identity(runtime(request).database, principal(request)))


@router.patch("/me")
def update_profile(request: Request, body: ProfileInput) -> dict[str, Any]:
    current = user_principal(request)
    name = body.display_name.strip()
    if not name:
        raise field_error("display_name", "invalid_name")
    ctx = runtime(request)
    with ctx.database.transaction(write=True) as session:
        user = session.get(User, current.user_id)
        if user is None:
            raise unauthorized()
        user.display_name = name
    return ok(identity(ctx.database, current))


@router.post("/auth/password/change")
def password_change(request: Request, response: Response, body: PasswordInput) -> dict[str, Any]:
    current = user_principal(request)
    ctx = _limit(request, action="change_password", account=current.user_id)
    accounts.change_password(
        ctx.database,
        user_id=current.user_id,
        current_password=body.current_password.get_secret_value(),
        password=body.password.get_secret_value(),
    )
    cookies.clear_session(ctx, response)
    return ok({"changed": True, "login_required": True})


@router.get("/auth/sessions")
def devices(request: Request) -> dict[str, Any]:
    return ok(sessions.list_sessions(runtime(request).database, user_principal(request)))


@router.delete("/auth/sessions/{session_id}")
def revoke_device(request: Request, response: Response, session_id: str) -> dict[str, Any]:
    current = user_principal(request)
    ctx = runtime(request)
    with ctx.database.transaction(write=True) as session:
        record = session.get(AuthSession, session_id, with_for_update=True)
        if record is None or record.user_id != current.user_id:
            raise ApiError("INVALID_INPUT", "Session not found", status_code=404)
        record.revoked_at = time.time()
    if session_id == current.session_id:
        cookies.clear_session(ctx, response)
    return ok({"revoked": True})
