"""Secure deployment settings, persistent signing keys and public log redaction."""

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("argon2")
pytest.importorskip("jwt")

from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from agentic_graphrag.webapp.app import create_app
from agentic_graphrag.webapp.logging import public_request_target
from agentic_graphrag.webapp.settings import WebSettings


@pytest.mark.parametrize("secret", [None, SecretStr(""), SecretStr("short")])
def test_secure_deployment_requires_a_strong_configured_secret(secret):
    with pytest.raises(ValidationError):
        WebSettings(
            secure_cookies=True,
            public_origin="https://example.com",
            secret_key=secret,
            _env_file=None,
        )


def test_development_key_persists_with_private_permissions(tmp_path):
    path = tmp_path / "signing.key"
    config = WebSettings(secret_file=str(path), _env_file=None)
    first = config.signing_secret()
    assert len(first) >= 32 and config.signing_secret() == first
    assert path.stat().st_mode & 0o777 == 0o600


def test_secure_cookie_host_prefix(web_settings, mailbox):
    config = web_settings.model_copy(
        update={"secure_cookies": True, "public_origin": "https://testserver"}
    )
    with TestClient(
        create_app(settings=config, mail=mailbox, include_business=False),
        base_url="https://testserver",
    ) as client:
        response = client.get("/auth/csrf")
        cookie = response.headers["set-cookie"]
        assert cookie.startswith("__Host-agr_csrf=")
        assert "Secure" in cookie and "HttpOnly" in cookie
        assert "Domain=" not in cookie


def test_request_logging_redacts_reset_and_invite_credentials():
    assert "secret" not in public_request_target("/reset?token=secret")
    assert "secret" not in public_request_target("/v1/invites/secret/accept")
    assert public_request_target("/app/chat?mode=fast") == "/app/chat?mode=fast"
