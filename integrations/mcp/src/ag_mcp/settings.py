"""Process configuration; credentials are never part of tool arguments."""

from ag_client.transport import ClientSettings
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AG_MCP_", extra="ignore")

    base_url: str = "http://localhost:8000"
    api_key: SecretStr | None = None
    kb_id: str = "default"
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)

    @model_validator(mode="after")
    def validate_connection(self) -> "Settings":
        ClientSettings(base_url=self.base_url, kb_id=self.kb_id, timeout=self.timeout_seconds)
        return self
