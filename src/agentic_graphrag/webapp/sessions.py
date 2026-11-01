"""Revocable sessions and single-use refresh credentials."""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agentic_graphrag.api.auth import Principal
from agentic_graphrag.api.rbac import Role
from agentic_graphrag.webapp.database import Database
from agentic_graphrag.webapp.models import AuthSession, Membership, RefreshToken, User
from agentic_graphrag.webapp.security import opaque_token, token_digest, unauthorized


@dataclass(frozen=True)
class SessionGrant:
    session_id: str
    user_id: str
    tenant_id: str
    version: int
    refresh_token: str
    expires_at: float


def new_session(session: Session, *, user_id: str, tenant_id: str, seconds: int) -> SessionGrant:
    record = AuthSession(user_id=user_id, tenant_id=tenant_id, expires_at=time.time() + seconds)
    session.add(record)
    session.flush()
    return rotate_token(session, record)


def rotate_token(session: Session, record: AuthSession) -> SessionGrant:
    raw = opaque_token()
    session.add(
        RefreshToken(
            token_hash=token_digest(raw),
            session_id=record.id,
            expires_at=record.expires_at,
        )
    )
    return SessionGrant(
        record.id, record.user_id, record.tenant_id, record.version, raw, record.expires_at
    )


def refresh_session(db: Database, token: str) -> SessionGrant:
    grant = None
    with db.transaction(write=True) as session:
        credential = session.get(RefreshToken, token_digest(token), with_for_update=True)
        if credential is None or credential.expires_at <= time.time():
            raise unauthorized()
        record = session.get(AuthSession, credential.session_id, with_for_update=True)
        if record is None or record.revoked_at is not None:
            raise unauthorized()
        if credential.used_at is not None:
            record.revoked_at = time.time()
        else:
            session_principal(session, record)
            credential.used_at = time.time()
            record.version += 1
            grant = rotate_token(session, record)
    # A replay revocation must commit before returning the error.
    if grant is None:
        raise unauthorized()
    return grant


def session_principal(session: Session, record: AuthSession) -> Principal:
    user = session.get(User, record.user_id)
    member = session.get(Membership, (record.user_id, record.tenant_id))
    if user is None or user.status != "active" or member is None:
        raise unauthorized()
    if record.revoked_at is not None or record.expires_at <= time.time():
        raise unauthorized()
    return Principal(
        user_id=user.id,
        tenant_id=record.tenant_id,
        role=Role(member.role),
        api_key="",
        session_id=record.id,
    )


def revoke_sessions(session: Session, user_id: str) -> None:
    session.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=time.time())
    )


def revoke_current(db: Database, refresh: str, *, session_id: str = "") -> None:
    with db.transaction(write=True) as session:
        token = session.get(RefreshToken, token_digest(refresh))
        session_ids = {session_id, token.session_id if token else ""} - {""}
        for current_id in session_ids:
            record = session.get(AuthSession, current_id, with_for_update=True)
            if record:
                record.revoked_at = time.time()


def list_sessions(db: Database, principal: Principal) -> list[dict[str, object]]:
    with db.transaction() as session:
        rows = session.scalars(
            select(AuthSession)
            .where(
                AuthSession.user_id == principal.user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > time.time(),
            )
            .order_by(AuthSession.created_at.desc())
        ).all()
        return [
            {
                "id": row.id,
                "tenant_id": row.tenant_id,
                "created_at": row.created_at,
                "expires_at": row.expires_at,
                "current": row.id == principal.session_id,
            }
            for row in rows
        ]
