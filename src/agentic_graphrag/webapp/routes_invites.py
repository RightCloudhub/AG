"""Invitation management and public preview with email-bound acceptance."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Request
from sqlalchemy import select

from agentic_graphrag.api.envelope import ok
from agentic_graphrag.webapp import invites
from agentic_graphrag.webapp.models import Invite, Tenant
from agentic_graphrag.webapp.runtime import WebRuntime, principal, runtime, user_principal
from agentic_graphrag.webapp.schemas_tenants import InviteInput
from agentic_graphrag.webapp.tenant_access import not_found, tenant_scope

router = APIRouter(tags=["invitations"])


@router.get("/tenants/{tenant_id}/invites")
def list_invites(request: Request, tenant_id: str) -> dict[str, Any]:
    with runtime(request).database.transaction() as session:
        tenant_scope(session, principal(request), tenant_id, admin=True)
        rows = session.scalars(
            select(Invite).where(Invite.tenant_id == tenant_id).order_by(Invite.created_at.desc())
        )
        return ok([invites.invite_data(row) for row in rows])


@router.post("/tenants/{tenant_id}/invites", status_code=201)
def create(
    request: Request, tasks: BackgroundTasks, body: InviteInput, *, tenant_id: str
) -> dict[str, Any]:
    ctx = runtime(request)
    data, token, tenant_name = invites.create_invite(
        ctx, principal(request), body, tenant_id=tenant_id
    )
    tasks.add_task(_send, ctx, data, token=token, tenant_name=tenant_name)
    return ok(data)


@router.delete("/tenants/{tenant_id}/invites/{invite_id}")
def revoke(request: Request, tenant_id: str, invite_id: str) -> dict[str, Any]:
    data, _token, _tenant_name = invites.manage_invite(
        runtime(request),
        principal(request),
        tenant_id=tenant_id,
        invite_id=invite_id,
        resend=False,
    )
    return ok(data)


@router.post("/tenants/{tenant_id}/invites/{invite_id}/resend")
def resend(
    request: Request, tasks: BackgroundTasks, *, tenant_id: str, invite_id: str
) -> dict[str, Any]:
    ctx = runtime(request)
    data, token, tenant_name = invites.manage_invite(
        ctx, principal(request), tenant_id=tenant_id, invite_id=invite_id, resend=True
    )
    tasks.add_task(_send, ctx, data, token=token, tenant_name=tenant_name)
    return ok(data)


@router.get("/invites/{token}")
def preview(request: Request, token: str) -> dict[str, Any]:
    with runtime(request).database.transaction() as session:
        invite = invites.token_invite(session, token)
        tenant = session.get(Tenant, invite.tenant_id)
        if tenant is None:
            raise not_found()
        return ok(
            {
                "tenant_name": tenant.name,
                "email": invite.email_normalized,
                "role": invite.role,
                "expires_at": invite.expires_at,
            }
        )


@router.post("/invites/{token}/accept")
def accept(request: Request, token: str) -> dict[str, Any]:
    return ok(invites.accept_invite(runtime(request), user_principal(request), token))


def _send(ctx: WebRuntime, data: dict[str, Any], *, token: str, tenant_name: str) -> None:
    link = f"{ctx.settings.public_origin}/invite/{quote(token, safe='')}"
    try:
        ctx.mail.send(
            data["email"],
            subject=f"Invitation to {tenant_name}",
            body=(
                f"Join {tenant_name} as {data['role']}:\n{link}\n\n"
                f"This link expires in {ctx.settings.invite_seconds // 86400} days."
            ),
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("Invitation delivery failed: %s", type(exc).__name__)
