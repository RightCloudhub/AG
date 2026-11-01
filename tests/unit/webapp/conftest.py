"""Web identity fixtures use a separate temporary database and no reasoning engine."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr


@dataclass
class Mailbox:
    messages: list[tuple[str, str, str]] = field(default_factory=list)

    def send(self, recipient: str, *, subject: str, body: str) -> None:
        self.messages.append((recipient, subject, body))


@pytest.fixture
def web_settings(tmp_path):
    from agentic_graphrag.webapp.settings import WebSettings

    return WebSettings(
        database_url=f"sqlite:///{tmp_path / 'identity.db'}",
        secret_key=SecretStr("isolated-test-signing-secret-32-characters"),
        public_origin="http://testserver",
        auth_attempts=100,
        _env_file=None,
    )


@pytest.fixture
def mailbox():
    return Mailbox()


@pytest.fixture
def web_app(web_settings, mailbox):
    from agentic_graphrag.webapp.app import create_app

    return create_app(settings=web_settings, mail=mailbox, include_business=False)


@pytest.fixture
def browser(web_app):
    with TestClient(web_app) as client:
        yield client


@pytest.fixture
def csrf_headers():
    def headers(client):
        response = client.get("/v1/auth/csrf")
        assert response.status_code == 200
        return {
            "Origin": "http://testserver",
            "X-CSRF-Token": response.json()["data"]["csrf_token"],
        }

    return headers


@pytest.fixture
def register_user(csrf_headers):
    def register(client, email="person@example.com"):
        response = client.post(
            "/v1/auth/register",
            headers=csrf_headers(client),
            json={
                "email": email,
                "password": "Correct-horse-42!",
                "display_name": "Alex",
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["data"]

    return register
