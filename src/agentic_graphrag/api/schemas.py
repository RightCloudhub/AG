"""Request / response schemas for HTTP API (NFR-07)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class QueryRequest(BaseModel):
    """POST /v1/query body (FR-API-01)."""

    question: str = Field(..., min_length=1, max_length=2000)
    max_hops: int | None = Field(default=None, ge=1, le=20)
    timeout_ms: int | None = Field(default=None, ge=100, le=600_000)
    force_agentic: bool = False

    @field_validator("question")
    @classmethod
    def strip_question(cls, v: str) -> str:
        q = v.strip()
        if not q:
            raise ValueError("question must not be blank")
        return q


class CostOut(BaseModel):
    llm_calls: int = 0
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class ClaimOut(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class ToolCallOut(BaseModel):
    tool: str
    reason: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    hits: list[str] = Field(default_factory=list)


class StepOut(BaseModel):
    hop: int
    sub_question: str
    depends_on: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallOut] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    conclusion: str = ""
    critic_action: str = ""


class EvidenceOut(BaseModel):
    """Evidence snippet for citation click-through in the trial UI."""

    id: str
    content: str = ""
    source: str = ""
    score: float = 0.0


class QueryResultData(BaseModel):
    """Reasoning-chain payload returned in envelope.data."""

    query_id: str
    question: str
    answer: str
    status: str
    route: str = "agentic"
    claims: list[ClaimOut] = Field(default_factory=list)
    steps: list[StepOut] = Field(default_factory=list)
    cost: CostOut = Field(default_factory=CostOut)
    explored_paths: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceOut] = Field(default_factory=list)
