"""Migration portability, seed idempotence and database-shared login rate limits."""

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("argon2")
pytest.importorskip("jwt")

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from agentic_graphrag.webapp.app import create_app
from agentic_graphrag.webapp.models import Base, Membership, User
from agentic_graphrag.webapp.seed import seed_demo


def test_migrations_match_models_and_seed_is_idempotent(web_app):
    db = web_app.state.identity.database
    db.upgrade()
    with db.engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    seed_demo(db, email="admin@example.com", password="Demo-password-42!")
    seed_demo(db, email="admin@example.com", password="A-different-password-42!")
    with db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(select(func.count()).select_from(Membership)) == 1


def test_login_limit_is_shared_between_app_instances(web_settings, mailbox, csrf_headers):
    settings = web_settings.model_copy(update={"auth_attempts": 2})
    first = TestClient(create_app(settings=settings, mail=mailbox, include_business=False))
    second = TestClient(create_app(settings=settings, mail=mailbox, include_business=False))
    payload = {"email": "missing@example.com", "password": "wrong"}
    assert first.post("/auth/login", headers=csrf_headers(first), json=payload).status_code == 401
    assert second.post("/auth/login", headers=csrf_headers(second), json=payload).status_code == 401
    assert first.post("/auth/login", headers=csrf_headers(first), json=payload).status_code == 429
