"""Structured reasoning chain models (FR-AN-02)."""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class QueryStatus(str, Enum):
    ANSWERED = "answered"
    PARTIAL = "partial"
    NO_ANSWER = "no_answer"


class ToolCallTrace(BaseModel):
    tool: str
    reason: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    hits: list[str] = Field(default_factory=list)


class Claim(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class ReasoningStep(BaseModel):
    hop: int
    sub_question: str
    depends_on: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    conclusion: str = ""
    critic_action: str = ""


class CostStats(BaseModel):
    llm_calls: int = 0
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class ReasoningChain(BaseModel):
    query_id: str = Field(default_factory=lambda: str(uuid4()))
    question: str
    route: str = "agentic"
    steps: list[ReasoningStep] = Field(default_factory=list)
    answer: str = ""
    claims: list[Claim] = Field(default_factory=list)
    status: QueryStatus = QueryStatus.NO_ANSWER
    cost: CostStats = Field(default_factory=CostStats)
    explored_paths: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def honest_fallback(self, reason: str = "insufficient evidence") -> None:
        paths = "; ".join(self.explored_paths[:20]) if self.explored_paths else "(none)"
        self.status = QueryStatus.NO_ANSWER
        self.answer = (
            f"无法基于现有知识回答。原因: {reason}。"
            f"已探索路径摘要: {paths}。"
        )
        self.claims = []
