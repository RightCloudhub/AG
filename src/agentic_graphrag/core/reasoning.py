"""Structured reasoning chain models + JSON Schema (FR-AN-02, P2-AG-06).

The contract is shared by API responses, audit storage, evaluation attribution,
and the trial UI. Schema version is embedded in every chain as
``schema_version`` and exported to ``configs/schema/reasoning_chain_v1.json``.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

SCHEMA_VERSION = "1.0.0"
SCHEMA_ID = "https://agentic-graphrag.local/schemas/reasoning_chain_v1.json"


class QueryStatus(StrEnum):
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
    hop: int = Field(..., ge=0, description="Hop index within the agentic loop")
    sub_question: str
    depends_on: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    conclusion: str = ""
    critic_action: str = Field(
        default="",
        description="sufficient|next_hop|rewrite|give_up (CriticAction value)",
    )


class CostStats(BaseModel):
    llm_calls: int = 0
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class ReasoningChain(BaseModel):
    """FR-AN-02 reasoning chain — single source of truth for P2-AG-06."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    query_id: str = Field(default_factory=lambda: str(uuid4()))
    question: str
    route: str = Field(default="agentic", description="agentic|fast_path|baseline")
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
        self.answer = f"无法基于现有知识回答。原因: {reason}。已探索路径摘要: {paths}。"
        self.claims = []

    def to_contract_dict(self) -> dict[str, Any]:
        """JSON-serializable dict conforming to the published schema."""
        return self.model_dump(mode="json")


def reasoning_chain_json_schema() -> dict[str, Any]:
    """Draft-2020-12 JSON Schema for ReasoningChain (P2-AG-06)."""
    schema = ReasoningChain.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = SCHEMA_ID
    schema["title"] = "AgenticGraphRAG ReasoningChain"
    schema["description"] = (
        "Reasoning chain contract (FR-AN-02 / P2-AG-06): sub-questions, tool calls, "
        "evidence, claims with citations, status, and cost."
    )
    # Advertise version constant for consumers
    props = schema.setdefault("properties", {})
    if "schema_version" in props:
        props["schema_version"]["const"] = SCHEMA_VERSION
        props["schema_version"]["default"] = SCHEMA_VERSION
    return schema


def validate_reasoning_chain(data: dict[str, Any] | ReasoningChain) -> ReasoningChain:
    """Validate a payload against the ReasoningChain model (schema enforcement)."""
    if isinstance(data, ReasoningChain):
        return data
    try:
        return ReasoningChain.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"reasoning chain schema validation failed: {exc}") from exc


def export_reasoning_chain_schema(path: str | Path) -> Path:
    """Write the JSON Schema file (used by docs / CI drift checks)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(reasoning_chain_json_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out
