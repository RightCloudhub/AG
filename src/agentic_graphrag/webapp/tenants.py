"""Tenant creation, session switching, and member administration."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import func, select, update

from agentic_graphrag.api.auth import Principal
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.webapp.database import Database
from agentic_graphrag.webapp.models import AuthSession, Membership, RefreshToken, Tenant, User
from agentic_graphrag.webapp.sessions import SessionGrant, rotate_token, session_principal
from agentic_graphrag.webapp.tenant_access import clean_name, not_found, tenant_data, tenant_scope


def create_tenant(db: Database, current: Principal, name: str) -> dict[str, Any]:
    with db.transaction(write=True) as session:
        tenant = Tenant(name=clean_name(name), kind="team", created_by=current.user_id)
        session.add(tenant)
        session.flush()
        session.add(Membership(user_id=current.user_id, tenant_id=tenant.id, role="admin"))
        session.flush()
        return tenant_data(session, tenant)


def switch_tenant(db: Database, current: Principal, tenant_id: str) -> SessionGrant:
    with db.transaction(write=True) as session:
        tenant = session.get(Tenant, tenant_id, with_for_update=True)
        member = session.get(Membership, (current.user_id, tenant_id))
        if tenant is None or member is None:
            raise not_found()
        record = session.get(AuthSession, current.session_id, with_for_update=True)
        if record is None or record.user_id != current.user_id:
            raise not_found()
        session_principal(session, record)
        record.tenant_id = tenant_id
        record.version += 1
        session.execute(
            update(RefreshToken)
            .where(RefreshToken.session_id == record.id, RefreshToken.used_at.is_(None))
            .values(used_at=time.time())
        )
        return rotate_token(session, record)


def list_members(db: Database, current: Principal, tenant_id: str) -> list[dict[str, Any]]:
    with db.transaction() as session:
        tenant_scope(session, current, tenant_id, admin=True)
        rows = session.execute(
            select(Membership, User)
            .join(User, Membership.user_id == User.id)
            .where(Membership.tenant_id == tenant_id)
            .order_by(Membership.joined_at, User.id)
        ).all()
        return [
            {
                "user_id": user.id,
                "email": user.email,
                "display_name": user.display_name,
                "role": member.role,
                "joined_at": member.joined_at,
            }
            for member, user in rows
        ]


def change_member(
    db: Database, current: Principal, *, tenant_id: str, user_id: str, role: str | None
) -> dict[str, Any]:
    with db.transaction(write=True) as session:
        # Every membership mutation locks the tenant first. PostgreSQL row locks
        # and SQLite BEGIN IMMEDIATE serialize the last-admin check with the write.
        tenant_scope(session, current, tenant_id, admin=True, lock=True)
        member = session.get(Membership, (user_id, tenant_id), with_for_update=True)
        if member is None:
            raise not_found()
        count = session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.tenant_id == tenant_id, Membership.role == "admin")
        )
        if member.role == "admin" and role != "admin" and count == 1:
            raise ApiError("LAST_ADMIN", "Keep at least one tenant administrator", status_code=409)
        if role is not None:
            member.role = role
        else:
            session.delete(member)
            session.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user_id, AuthSession.tenant_id == tenant_id)
                .values(revoked_at=time.time())
            )
        return {"user_id": user_id, "role": role, "removed": role is None}
