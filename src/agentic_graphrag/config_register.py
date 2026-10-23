"""Search-guidance options; these do not select or modify retrieval backends."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RequirementRegisterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    seed_enabled: bool = True
    updates_enabled: bool = True
    delta_enabled: bool = True
    verifier_enabled: bool = True
    answer_policy: Literal["partial", "abstain"] = "partial"
    seed_top_k: int = Field(default=5, ge=1)
    max_rounds: int = Field(default=15, ge=1, le=100)
    context_limit_tokens: int = Field(default=128_000, ge=1)
    convergence_ratio: float = Field(default=0.8, gt=0, le=1)
    max_requirements: int = Field(default=32, ge=1, le=100)
