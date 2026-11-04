"""Deployment-owned model catalogue; administrators bind roles to catalogue IDs."""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentic_graphrag.core.models import ModelCapabilities, ModelRole


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    secret_ref: str | None = None
    auth_required: bool = True
    allowed_tenants: list[str] = Field(default_factory=lambda: ["default"])
    timeout_seconds: float = Field(default=30, gt=0, le=120)

    @field_validator("base_url")
    @classmethod
    def safe_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Provider endpoint must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Provider endpoint cannot contain credentials, query or fragment")
        return value.rstrip("/")

    @model_validator(mode="after")
    def require_secret_reference(self) -> ProviderConfig:
        if self.auth_required and not self.secret_ref:
            raise ValueError("Authenticated provider requires a secret_ref")
        if not self.allowed_tenants:
            raise ValueError("Provider must be assigned to at least one tenant")
        return self


class ModelTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    revision: str = Field(default="1", min_length=1, max_length=80)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)


class ModelRoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    catalog: dict[str, ModelTarget] = Field(default_factory=dict)
    roles: dict[ModelRole, str | None] = Field(default_factory=dict)
    profile_revision: str = "legacy"
    scope_key: str = "default"

    @model_validator(mode="after")
    def validate_references(self) -> ModelRoutingConfig:
        for target in self.catalog.values():
            if target.provider not in self.providers:
                raise ValueError("Model refers to an unregistered provider")
        for target_id in self.roles.values():
            if target_id is not None and target_id not in self.catalog:
                raise ValueError("Role refers to an unregistered model")
        return self
