"""Settings owned by the optional Web application, separate from core configuration."""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agentic_graphrag.config import resolve_path


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    database_url: str = Field("sqlite:///data/identity/webapp.db", alias="AGR_DATABASE_URL")
    secret_key: SecretStr | None = Field(None, alias="AGR_WEB_SECRET")
    secret_file: str = Field("data/identity/signing.key", alias="AGR_WEB_SECRET_FILE")
    public_origin: str = Field("http://localhost:8000", alias="AGR_WEB_ORIGIN")
    secure_cookies: bool = Field(False, alias="AGR_WEB_SECURE_COOKIES")
    auto_migrate: bool = Field(True, alias="AGR_WEB_AUTO_MIGRATE")
    access_seconds: int = Field(900, ge=30, le=3600, alias="AGR_WEB_ACCESS_SECONDS")
    refresh_seconds: int = Field(604800, ge=300, alias="AGR_WEB_REFRESH_SECONDS")
    invite_seconds: int = Field(604800, ge=300, alias="AGR_WEB_INVITE_SECONDS")
    auth_attempts: int = Field(10, ge=1, alias="AGR_WEB_AUTH_ATTEMPTS")
    auth_window_seconds: int = Field(300, ge=1, alias="AGR_WEB_AUTH_WINDOW_SECONDS")
    mail_backend: str = Field("log", alias="AGR_WEB_MAIL_BACKEND")
    mail_from: str = Field("noreply@localhost", alias="AGR_WEB_MAIL_FROM")
    smtp_host: str = Field("", alias="AGR_WEB_SMTP_HOST")
    smtp_port: int = Field(587, ge=1, le=65535, alias="AGR_WEB_SMTP_PORT")
    smtp_user: str = Field("", alias="AGR_WEB_SMTP_USER")
    smtp_password: SecretStr = Field(SecretStr(""), alias="AGR_WEB_SMTP_PASSWORD")
    demo_password: SecretStr | None = Field(None, alias="AGR_DEMO_PASSWORD")

    @field_validator("public_origin")
    @classmethod
    def origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("AGR_WEB_ORIGIN must be an absolute HTTP(S) origin")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("AGR_WEB_ORIGIN must not include credentials, a path or query")
        return value.rstrip("/")

    @model_validator(mode="after")
    def production_settings(self) -> WebSettings:
        if self.secret_key is not None and len(self.secret_key.get_secret_value()) < 32:
            raise ValueError("AGR_WEB_SECRET must contain at least 32 characters")
        if self.secure_cookies and not self.public_origin.startswith("https://"):
            raise ValueError("Secure cookies require an HTTPS public origin")
        if self.secure_cookies and self.secret_key is None:
            raise ValueError("Set AGR_WEB_SECRET when secure cookies are enabled")
        if self.mail_backend not in {"log", "smtp"}:
            raise ValueError("AGR_WEB_MAIL_BACKEND must be log or smtp")
        if self.mail_backend == "smtp" and not self.smtp_host:
            raise ValueError("SMTP mail requires AGR_WEB_SMTP_HOST")
        return self

    def signing_secret(self) -> str:
        if self.secret_key:
            return self.secret_key.get_secret_value()
        path = Path(resolve_path(self.secret_file))
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent) as file:
            file.write(secrets.token_urlsafe(48))
            file.flush()
            try:
                os.link(file.name, path)
            except FileExistsError:
                pass
        secret = path.read_text(encoding="utf-8").strip()
        if len(secret) < 32:
            raise ValueError("Development signing key is invalid")
        return secret
