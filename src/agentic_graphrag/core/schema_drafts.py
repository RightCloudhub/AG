"""Source-backed Schema proposals and explicit human approval contracts."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.core.knowledge import KnowledgeScope, SourceRef


class RelationProposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
    predicate: str
    aliases: tuple[str, ...] = ()
    head_types: tuple[str, ...]
    tail_types: tuple[str, ...]
    document_ids: tuple[str, ...]
    distinct_pairs: int
    relation_ids: tuple[str, ...]
    descriptions: tuple[str, ...]
    examples: tuple[SourceRef, ...] = ()

    @property
    def document_support(self) -> int:
        return len(self.document_ids)


class SchemaDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
    draft_id: str
    scope: KnowledgeScope
    generation: str
    input_fingerprint: str
    relations: tuple[RelationProposal, ...]
    entity_types: tuple[str, ...]
    relation_mapping: dict[str, str]
    entity_mapping: dict[str, str]
    schema_definition: dict[str, Any] = Field(alias="schema")
    status: Literal["draft", "approved", "published"] = "draft"
    schema_id: str | None = None
    schema_version: str | None = None
    reviewer: str = ""
    approved_at: str = ""
    published_generation: str | None = None


class DraftApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    schema_version: str = Field(min_length=1, max_length=100)
    reviewer: str = Field(min_length=1, max_length=200)
    schema_definition: dict[str, Any] = Field(alias="schema")
    relation_mapping: dict[str, str]
    entity_mapping: dict[str, str]


class SchemaRegistry(Protocol):
    def save_draft(self, draft: SchemaDraft) -> SchemaDraft: ...

    def get_draft(self, scope: KnowledgeScope, draft_id: str) -> SchemaDraft | None: ...

    def list_drafts(self, scope: KnowledgeScope) -> list[SchemaDraft]: ...

    def approve(
        self, scope: KnowledgeScope, draft_id: str, *, approval: DraftApproval
    ) -> SchemaDraft: ...

    def mark_published(self, scope: KnowledgeScope, draft_id: str, *, generation: str) -> None: ...

    def get_schema(
        self, scope: KnowledgeScope, *, schema_id: str, version: str
    ) -> dict[str, Any] | None: ...

    def close(self) -> None: ...
