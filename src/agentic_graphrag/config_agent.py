"""Opt-in supervisor/worker limits; never enable live services implicitly."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["single", "multi"] = "single"
    max_parallel: int = Field(default=1, ge=1, le=16)
    max_tool_calls: int = Field(default=3, ge=1, le=12)
    max_research_calls: int = Field(default=2, ge=1, le=6)
    branch_tokens: int = Field(default=12000, ge=1)
    writer_reserve_tokens: int = Field(default=8000, ge=1)
    writer_reserve_calls: int = Field(default=4, ge=1)
    verifier_reserve_tokens: int = Field(default=4000, ge=1)
    max_output_tokens: int = Field(default=1500, ge=1)
