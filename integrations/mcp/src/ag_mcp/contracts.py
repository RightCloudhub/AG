"""Explicit tool schemas. Unknown inputs cannot override server identity or budgets."""

from typing import Literal

from ag_client import (
    EvidenceResult,
    KnowledgeBasePage,
    QueryAudit,
    QueryResult,
    SearchAudit,
    SearchResult,
)
from ag_client.requests import Identifier, Question, Strategy
from mcp.types import Tool, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListInput(ToolInput):
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=50, ge=1, le=100)


class ScopedInput(ToolInput):
    kb_id: Identifier | None = Field(
        default=None, description="Knowledge base within the API-key tenant"
    )


class QueryInput(ScopedInput):
    question: Question
    strategy: Strategy = "auto"
    doc_ids: list[Identifier] | None = Field(default=None, max_length=1000)
    max_hops: int | None = Field(default=None, ge=1, le=20)
    timeout_ms: int | None = Field(default=None, ge=100, le=600_000)


class SearchInput(ScopedInput):
    query: Question
    top_k: int = Field(default=10, ge=1, le=50)
    doc_ids: list[Identifier] | None = Field(default=None, max_length=1000)


class EvidenceInput(ScopedInput):
    evidence_id: Identifier
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4096, ge=1, le=4096)


class AuditInput(ScopedInput):
    audit_id: Identifier
    kind: Literal["query", "search"] = "query"


class AuditResult(BaseModel):
    kind: Literal["query", "search"]
    record: QueryAudit | SearchAudit


TOOL_MODELS: dict[str, tuple[type[BaseModel], type[BaseModel], str]] = {
    "list_knowledge_bases": (
        ListInput,
        KnowledgeBasePage,
        "List the authenticated tenant's accessible knowledge bases.",
    ),
    "search": (
        SearchInput,
        SearchResult,
        "Retrieve limited source evidence without composing an answer; requires durable audit.",
    ),
    "query": (
        QueryInput,
        QueryResult,
        "Answer with source citations and a durable audit ID. May incur model usage charges.",
    ),
    "get_evidence": (
        EvidenceInput,
        EvidenceResult,
        "Read a bounded original source excerpt and its document version.",
    ),
    "get_audit": (
        AuditInput,
        AuditResult,
        "Inspect the evidence and execution receipt of a query or search.",
    ),
}


def tool_definitions() -> list[Tool]:
    return [
        Tool(
            name=name,
            description=description,
            inputSchema=inputs.model_json_schema(),
            outputSchema=outputs.model_json_schema(),
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=name not in {"query", "search"},
                openWorldHint=False,
            ),
        )
        for name, (inputs, outputs, description) in TOOL_MODELS.items()
    ]
