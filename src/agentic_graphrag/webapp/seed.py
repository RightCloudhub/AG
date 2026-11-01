"""Idempotently create a demo tenant and admin; never ships a fixed password."""

from __future__ import annotations

import argparse
import getpass

from sqlalchemy import select

from agentic_graphrag.webapp.database import Database
from agentic_graphrag.webapp.models import Membership, Tenant, User
from agentic_graphrag.webapp.security import hash_password, normalize_email
from agentic_graphrag.webapp.settings import WebSettings


def seed_demo(db: Database, *, email: str, password: str, display_name: str = "Demo admin") -> None:
    normalized = normalize_email(email)
    encoded = hash_password(password)
    with db.transaction(write=True) as session:
        user = session.scalar(select(User).where(User.email_normalized == normalized))
        if user is None:
            user = User(
                email=normalized,
                email_normalized=normalized,
                display_name=display_name,
                password_hash=encoded,
            )
            session.add(user)
            session.flush()
        tenant = session.get(Tenant, "demo")
        if tenant is None:
            tenant = Tenant(id="demo", name="Demo", kind="team", created_by=user.id)
            session.add(tenant)
            session.flush()
        member = session.get(Membership, (user.id, tenant.id))
        if member is None:
            session.add(Membership(user_id=user.id, tenant_id=tenant.id, role="admin"))
        elif member.role != "admin":
            raise ValueError("Existing membership is not an admin; seed does not change roles")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    config = WebSettings()
    password = (
        config.demo_password.get_secret_value()
        if config.demo_password
        else getpass.getpass("Demo admin password: ")
    )
    db = Database(config.database_url)
    try:
        db.upgrade()
        seed_demo(db, email=args.email, password=password)
    finally:
        db.close()
    print("Demo tenant and admin are ready (existing passwords are unchanged).")


if __name__ == "__main__":
    main()
