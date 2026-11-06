"""Calibrated review automation is explicit and disabled until real evidence exists."""

from pydantic import BaseModel, ConfigDict, Field


class ReviewAutomationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    minimum_reviewed: int = Field(default=200, ge=1)
    max_false_approval_upper: float = Field(default=0.01, gt=0, lt=1)
    sample_rate: float = Field(default=0.2, gt=0, le=1)
    relation_allowlist: tuple[str, ...] = ()
