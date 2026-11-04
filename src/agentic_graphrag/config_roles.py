"""Per-role model overrides and explicit USD prices; unset prices stay unknown."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type ModelRole = Literal["triage", "researcher", "verifier", "writer"]


class RoleModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = Field(default=None, min_length=1)
    input_usd_per_million: float | None = Field(default=None, ge=0)
    output_usd_per_million: float | None = Field(default=None, ge=0)

    def cost(self, prompt: int, completion: int) -> float | None:
        if prompt + completion == 0:
            return 0.0
        if self.input_usd_per_million is None or self.output_usd_per_million is None:
            return None
        return (
            prompt * self.input_usd_per_million + completion * self.output_usd_per_million
        ) / 1e6


class RolesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    triage: RoleModelConfig = Field(default_factory=RoleModelConfig)
    researcher: RoleModelConfig = Field(default_factory=RoleModelConfig)
    verifier: RoleModelConfig = Field(default_factory=RoleModelConfig)
    writer: RoleModelConfig = Field(default_factory=RoleModelConfig)

    def for_role(self, role: ModelRole) -> RoleModelConfig:
        return getattr(self, role)  # type: ignore[no-any-return]
