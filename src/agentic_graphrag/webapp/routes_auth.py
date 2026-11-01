"""Public authentication endpoints and Cookie issuance."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request, Response

from agentic_graphrag.api.envelope import ok
from agentic_graphrag.webapp import accounts, cookies, sessions
from agentic_graphrag.webapp.identity import identity
from agentic_graphrag.webapp.models import AuthSession
from agentic_graphrag.webapp.rate_limit import check_auth_limit
from agentic_graphrag.webapp.runtime import WebRuntime, runtime
from agentic_graphrag.webapp.schemas import EmailInput, LoginInput, RegisterInput, ResetInput
from agentic_graphrag.webapp.security import normalize_email, unauthorized

router = APIRouter(prefix="/auth", tags=["authentication"])


def _limit(request: Request, *, action: str, account: str = "") -> WebRuntime:
    ctx = runtime(request)
    check_auth_limit(
        ctx.database,
        ctx.settings,
        action=action,
        ip=request.client.host if request.client else "unknown",
        account=account,
    )
    return ctx


def grant_response(
    ctx: WebRuntime, response: Response, grant: sessions.SessionGrant
) -> dict[str, Any]:
    with ctx.database.transaction() as session:
        record = session.get(AuthSession, grant.session_id)
        if record is None:
            raise unauthorized()
        current = sessions.session_principal(session, record)
    data = identity(ctx.database, current)
    data["csrf_token"] = cookies.issue_session(ctx, response, grant)
    return ok(data)


@router.get("/csrf")
def csrf(request: Request, response: Response) -> dict[str, Any]:
    ctx = runtime(request)
    token = cookies.bootstrap_csrf(ctx, request, response)
    return ok({"csrf_token": token})


@router.post("/register", status_code=201)
def register(request: Request, response: Response, body: RegisterInput) -> dict[str, Any]:
    ctx = _limit(request, action="register", account=body.email)
    grant = accounts.register(
        ctx.database,
        ctx.settings,
        email=body.email,
        password=body.password.get_secret_value(),
        display_name=body.display_name,
    )
    return grant_response(ctx, response, grant)


@router.post("/login")
def login(request: Request, response: Response, body: LoginInput) -> dict[str, Any]:
    ctx = _limit(request, action="login", account=body.email)
    grant = accounts.login(
        ctx.database, ctx.settings, email=body.email, password=body.password.get_secret_value()
    )
    return grant_response(ctx, response, grant)


@router.post("/refresh")
def refresh(request: Request, response: Response) -> dict[str, Any]:
    ctx = runtime(request)
    grant = sessions.refresh_session(
        ctx.database, cookies.read_cookie(ctx, request, cookies.REFRESH)
    )
    return grant_response(ctx, response, grant)


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    ctx = runtime(request)
    current = getattr(request.state, "principal", None)
    sessions.revoke_current(
        ctx.database,
        cookies.read_cookie(ctx, request, cookies.REFRESH),
        session_id=current.session_id if current else "",
    )
    cookies.clear_session(ctx, response)
    return ok({"logged_out": True})


@router.post("/forgot")
def forgot(request: Request, body: EmailInput, tasks: BackgroundTasks) -> dict[str, Any]:
    ctx = _limit(request, action="forgot", account=body.email)
    tasks.add_task(
        accounts.forgot_password,
        ctx.database,
        ctx.settings,
        email=normalize_email(body.email),
        mail=ctx.mail,
    )
    return ok({"accepted": True})


@router.post("/reset")
def reset(request: Request, response: Response, body: ResetInput) -> dict[str, Any]:
    ctx = _limit(request, action="reset")
    accounts.reset_password(
        ctx.database, token=body.token, password=body.password.get_secret_value()
    )
    cookies.clear_session(ctx, response)
    return ok({"reset": True})
