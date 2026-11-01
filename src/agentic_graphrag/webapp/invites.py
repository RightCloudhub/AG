"""Single-use invitations, bound to both tenant and recipient email."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_graphrag.api.auth import Principal
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.webapp.models import Invite, Membership, Tenant, User
from agentic_graphrag.webapp.runtime import WebRuntime
from agentic_graphrag.webapp.schemas_tenants import InviteInput
from agentic_graphrag.webapp.security import normalize_email, opaque_token, token_digest
from agentic_graphrag.webapp.tenant_access import actor_user, not_found, tenant_scope


def invite_data(invite: Invite) -> dict[str, Any]:
    status = "pending"
    if invite.accepted_at is not None:
        status = "accepted"
    elif invite.revoked_at is not None:
        status = "revoked"
    elif invite.expires_at <= time.time():
        status = "expired"
    return {
        "id": invite.id,
        "email": invite.email_normalized,
        "role": invite.role,
        "created_at": invite.created_at,
        "expires_at": invite.expires_at,
        "status": status,
    }


def create_invite(
    ctx: WebRuntime, current: Principal, body: InviteInput, *, tenant_id: str
) -> tuple[dict[str, Any], str, str]:
    email, token = normalize_email(body.email), opaque_token()
    with ctx.database.transaction(write=True) as session:
        tenant = tenant_scope(session, current, tenant_id, admin=True, lock=True)
        _check_recipient(session, tenant_id, email)
        invite = Invite(
            tenant_id=tenant_id,
            email_normalized=email,
            role=body.role.value,
            token_hash=token_digest(token),
            invited_by=actor_user(current),
            expires_at=time.time() + ctx.settings.invite_seconds,
        )
        session.add(invite)
        session.flush()
        return invite_data(invite), token, tenant.name


def _check_recipient(session: Session, tenant_id: str, email: str) -> None:
    member = session.scalar(
        select(Membership)
        .join(User)
        .where(
            Membership.tenant_id == tenant_id,
            User.email_normalized == email,
        )
    )
    if member is not None:
        raise ApiError("ALREADY_MEMBER", "This person is already a member", status_code=409)
    pending = session.scalar(
        select(Invite).where(
            Invite.tenant_id == tenant_id,
            Invite.email_normalized == email,
            Invite.accepted_at.is_(None),
            Invite.revoked_at.is_(None),
            Invite.expires_at > time.time(),
        )
    )
    if pending is not None:
        raise ApiError("INVITE_PENDING", "An invitation is already pending", status_code=409)


def manage_invite(
    ctx: WebRuntime, current: Principal, *, tenant_id: str, invite_id: str, resend: bool
) -> tuple[dict[str, Any], str, str]:
    with ctx.database.transaction(write=True) as session:
        tenant = tenant_scope(session, current, tenant_id, admin=True, lock=True)
        invite = session.get(Invite, invite_id, with_for_update=True)
        if invite is None or invite.tenant_id != tenant_id or invite.accepted_at is not None:
            raise not_found()
        token = ""
        if resend:
            if invite.revoked_at is not None:
                raise not_found()
            token = opaque_token()
            invite.token_hash = token_digest(token)
            invite.expires_at = time.time() + ctx.settings.invite_seconds
        else:
            invite.revoked_at = time.time()
        return invite_data(invite), token, tenant.name


def token_invite(session: Session, token: str, *, lock: bool = False) -> Invite:
    digest = token_digest(token)
    row = session.scalar(select(Invite).where(Invite.token_hash == digest))
    if row is None:
        raise not_found()
    if lock:
        session.get(Tenant, row.tenant_id, with_for_update=True)
        session.refresh(row, with_for_update=True)
    if row.token_hash != digest or invite_data(row)["status"] != "pending":
        raise not_found()
    return row


def accept_invite(ctx: WebRuntime, current: Principal, token: str) -> dict[str, Any]:
    with ctx.database.transaction(write=True) as session:
        invite = token_invite(session, token, lock=True)
        user = session.get(User, current.user_id)
        if user is None or user.email_normalized != invite.email_normalized:
            raise ApiError(
                "INVITE_EMAIL_MISMATCH", "Sign in with the invited email", status_code=403
            )
        member = session.get(Membership, (current.user_id, invite.tenant_id))
        if member is None:
            member = Membership(
                user_id=current.user_id, tenant_id=invite.tenant_id, role=invite.role
            )
            session.add(member)
        # Consuming an invitation never upgrades a membership created since it was sent.
        invite.accepted_at = time.time()
        return {"accepted": True, "tenant_id": invite.tenant_id, "role": member.role}
