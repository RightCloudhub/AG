"""Validated request types shared by synchronous and asynchronous clients."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Strategy = Literal["auto", "local", "global"]


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Question
    kb_id: Identifier = "default"
    doc_ids: list[Identifier] | None = Field(default=None, max_length=1000)
    strategy: Strategy = "auto"
    audit_required: bool = False
    force_agentic: bool = False
    max_hops: int | None = Field(default=None, ge=1, le=20)
    timeout_ms: int | None = Field(default=None, ge=100, le=600_000)

    @model_validator(mode="after")
    def check_strategy(self) -> "QueryRequest":
        if self.force_agentic and self.strategy == "global":
            raise ValueError("force_agentic and global strategy cannot be combined")
        return self


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Question
    kb_id: Identifier = "default"
    doc_ids: list[Identifier] | None = Field(default=None, max_length=1000)
    top_k: int = Field(default=10, ge=1, le=50)
    audit_required: bool = False
