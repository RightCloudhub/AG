"""Request / response schemas for HTTP API (NFR-07)."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_graphrag.core.enums import CriticAction, QueryRoute, StepAction, ToolName
from agentic_graphrag.core.reasoning import QueryStatus

_REPEATED_SENTENCE = re.compile(r"([^.!?]{3,}[.!?])(?:\s*\1)+", re.IGNORECASE)


class QueryRequest(BaseModel):
    """POST /v1/query body (FR-API-01)."""

    question: str = Field(..., min_length=1, max_length=2000)
    max_hops: int | None = Field(default=None, ge=1, le=20)
    timeout_ms: int | None = Field(default=None, ge=100, le=600_000)
    force_agentic: bool = False
    kb_id: str = Field(default="default", min_length=1, max_length=128, pattern=r"^[\w.-]+$")
    doc_ids: list[str] | None = Field(default=None, max_length=10000)
    strategy: Literal["auto", "local", "global"] = "auto"
    audit_required: bool = False

    @model_validator(mode="after")
    def validate_selection(self) -> QueryRequest:
        if self.strategy == "global" and self.force_agentic:
            raise ValueError("strategy=global conflicts with force_agentic")
        if self.doc_ids is not None:
            if not self.doc_ids or any(not value.strip() for value in self.doc_ids):
                raise ValueError("doc_ids must contain nonempty document IDs")
            self.doc_ids = list(dict.fromkeys(self.doc_ids))
        return self

    @field_validator("question", mode="before")
    @classmethod
    def collapse_repeats(cls, v: object) -> object:
        """Collapse consecutive duplicate sentences (padding) before the length cap.

        "What is the parent of X? please investigate further. please investigate
        further. ..." is one question plus filler; rejecting it with 422 helps no
        one. Genuinely long, non-repetitive input is still rejected by max_length.
        """
        if not isinstance(v, str):
            return v
        return _REPEATED_SENTENCE.sub(r"\1", v)

    @field_validator("question")
    @classmethod
    def strip_question(cls, v: str) -> str:
        q = v.strip()
        if not q:
            raise ValueError("question must not be blank")
        return q


class CostOut(BaseModel):
    llm_calls: int = 0
    embed_calls: int = 0
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class ClaimOut(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class ToolCallOut(BaseModel):
    tool: ToolName
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
    critic_action: CriticAction | StepAction | Literal[""] = ""


class EvidenceOut(BaseModel):
    """Evidence snippet for citation click-through in the trial UI."""

    id: str
    content: str = ""
    source: str = ""
    score: float = 0.0
    evidence_id: str | None = None
    source_refs: list[dict[str, Any]] = Field(default_factory=list)


class DegradedChannelOut(BaseModel):
    """One retrieval channel that degraded or failed during the run (O-9).

    Reported per query so a caller can tell "no evidence exists" from "retrieval
    was broken": ``failed`` means the channel produced nothing at all.
    """

    channel: str
    reason: str = ""
    status: str = "degraded"


class QueryResultData(BaseModel):
    """Reasoning-chain payload returned in envelope.data."""

    query_id: str
    question: str
    answer: str
    status: QueryStatus
    route: QueryRoute = QueryRoute.AGENTIC
    claims: list[ClaimOut] = Field(default_factory=list)
    steps: list[StepOut] = Field(default_factory=list)
    cost: CostOut = Field(default_factory=CostOut)
    explored_paths: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceOut] = Field(default_factory=list)
    degraded_channels: list[DegradedChannelOut] = Field(default_factory=list)
    # True when the configured channels all failed: the answer is the best
    # available guess, not a retrieved result (see app.degradation_policy).
    low_confidence: bool = False
    audit_status: Literal["persisted", "failed", "disabled"] = "disabled"
