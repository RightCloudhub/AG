"""Account transactions, kept independent of the reasoning service."""

from __future__ import annotations

import logging
import time

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from agentic_graphrag.webapp.database import Database
from agentic_graphrag.webapp.mail import MailSender
from agentic_graphrag.webapp.models import Membership, PasswordResetToken, Tenant, User
from agentic_graphrag.webapp.security import (
    field_error,
    hash_password,
    normalize_email,
    opaque_token,
    token_digest,
    unauthorized,
    verify_password,
)
from agentic_graphrag.webapp.sessions import SessionGrant, new_session, revoke_sessions
from agentic_graphrag.webapp.settings import WebSettings


def register(
    db: Database,
    settings: WebSettings,
    *,
    email: str,
    password: str,
    display_name: str,
) -> SessionGrant:
    email = normalize_email(email)
    name = display_name.strip()
    if not name or len(name) > 100:
        raise field_error("display_name", "invalid_name")
    encoded = hash_password(password)
    try:
        with db.transaction(write=True) as session:
            user = User(
                email=email, email_normalized=email, display_name=name, password_hash=encoded
            )
            session.add(user)
            session.flush()
            tenant = Tenant(name=f"{name[:90]}'s workspace", kind="personal", created_by=user.id)
            session.add(tenant)
            session.flush()
            session.add(Membership(user_id=user.id, tenant_id=tenant.id, role="admin"))
            return new_session(
                session, user_id=user.id, tenant_id=tenant.id, seconds=settings.refresh_seconds
            )
    except IntegrityError as exc:
        raise field_error("email", "email_taken") from exc


def login(db: Database, settings: WebSettings, *, email: str, password: str) -> SessionGrant:
    normalized = normalize_email(email)
    with db.transaction() as session:
        user = session.scalar(select(User).where(User.email_normalized == normalized))
        encoded = user.password_hash if user else None
    if not verify_password(password, encoded) or user is None or user.status != "active":
        raise unauthorized()
    with db.transaction(write=True) as session:
        current = session.get(User, user.id, with_for_update=True)
        if current is None or current.password_hash != encoded or current.status != "active":
            raise unauthorized()
        member = session.scalar(
            select(Membership)
            .where(
                Membership.user_id == user.id,
            )
            .order_by(Membership.joined_at, Membership.tenant_id)
        )
        if member is None:
            raise unauthorized()
        return new_session(
            session, user_id=user.id, tenant_id=member.tenant_id, seconds=settings.refresh_seconds
        )


def forgot_password(db: Database, settings: WebSettings, *, email: str, mail: MailSender) -> None:
    normalized = normalize_email(email)
    raw = opaque_token()
    with db.transaction(write=True) as session:
        user = session.scalar(
            select(User).where(User.email_normalized == normalized, User.status == "active")
        )
        if user is None:
            return
        session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=time.time())
        )
        session.add(
            PasswordResetToken(
                token_hash=token_digest(raw), user_id=user.id, expires_at=time.time() + 1800
            )
        )
        recipient = user.email
    try:
        mail.send(
            recipient,
            subject="Reset your AGR password",
            body=f"{settings.public_origin}/reset?token={raw}",
        )
    except Exception:
        # The public response must not reveal whether an account exists or mail failed.
        logging.getLogger(__name__).error("Password reset delivery failed")


def reset_password(db: Database, *, token: str, password: str) -> None:
    encoded = hash_password(password)
    with db.transaction(write=True) as session:
        record = session.get(PasswordResetToken, token_digest(token), with_for_update=True)
        if record is None or record.used_at is not None or record.expires_at <= time.time():
            raise field_error("token", "invalid_token")
        user = session.get(User, record.user_id, with_for_update=True)
        if user is None or user.status != "active":
            raise field_error("token", "invalid_token")
        user.password_hash = encoded
        record.used_at = time.time()
        revoke_sessions(session, user.id)


def change_password(db: Database, *, user_id: str, current_password: str, password: str) -> None:
    encoded = hash_password(password)
    with db.transaction(write=True) as session:
        user = session.get(User, user_id, with_for_update=True)
        if user is None or not verify_password(current_password, user.password_hash):
            raise field_error("current_password", "invalid_password")
        user.password_hash = encoded
        revoke_sessions(session, user.id)
        session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=time.time())
        )
