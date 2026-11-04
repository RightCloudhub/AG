"""Knowledge ownership, configuration and immutable source references."""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_KNOWLEDGE_BASE = "default"
INITIAL_GENERATION = "1"
_PUBLIC_ID = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class KnowledgeScope(BaseModel):
    """An authorized tenant and one explicitly selected knowledge base."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tenant_id: str = Field(default="default", min_length=1, max_length=200)
    kb_id: str = Field(default=DEFAULT_KNOWLEDGE_BASE, pattern=_PUBLIC_ID)

    @field_validator("tenant_id")
    @classmethod
    def public_tenant(cls, value: str) -> str:
        if value != value.strip() or _CONTROL.search(value):
            raise ValueError("Tenant must not contain controls or surrounding whitespace")
        return value


class KnowledgeBase(KnowledgeScope):
    name: str = Field(default="Default", min_length=1, max_length=200)
    build_mode: Literal["schema", "open"] = "schema"
    schema_id: str | None = None
    schema_version: str | None = None
    model_profile_id: str | None = None
    config_revision: int = Field(default=1, ge=1)
    active_generation: str = Field(default=INITIAL_GENERATION, pattern=_PUBLIC_ID)
    review_policy: Literal["confidence", "required"] = "confidence"
    global_summary_enabled: bool = False
    rule_planner_profile: str | None = "legacy-company"

    @property
    def scope(self) -> KnowledgeScope:
        return KnowledgeScope(tenant_id=self.tenant_id, kb_id=self.kb_id)

    @model_validator(mode="after")
    def valid_mode(self) -> Self:
        if self.build_mode == "open" and self.schema_id is not None:
            raise ValueError("An open knowledge base cannot bind a Schema")
        return self


class SourceRef(BaseModel):
    """Server-derived normalized-text span, pinned to a document version."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    scope: KnowledgeScope
    doc_id: str = Field(min_length=1, max_length=512)
    doc_version: int = Field(ge=1)
    chunk_id: str = Field(min_length=1, max_length=512)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    span_start: int = Field(default=0, ge=0)
    span_end: int | None = Field(default=None, ge=0)
    page: int | None = Field(default=None, ge=1)
    paragraph: str | None = None

    @model_validator(mode="after")
    def valid_span(self) -> Self:
        if self.span_end is not None and self.span_end < self.span_start:
            raise ValueError("Source span ends before it starts")
        return self


class SummaryFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    text: str = Field(min_length=1)
    sources: tuple[SourceRef, ...]
    importance: float = Field(default=0.5, ge=0, le=1)


class SummaryRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    summary_id: str
    scope: KnowledgeScope
    generation: str
    kind: Literal["document", "community"]
    findings: tuple[SummaryFinding, ...] = ()
    document_ids: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    input_fingerprint: str
    model_revision: str
    prompt_version: str
    status: Literal["pending", "ready", "partial", "failed"] = "pending"

    @model_validator(mode="after")
    def same_scope(self) -> Self:
        if any(source.scope != self.scope for item in self.findings for source in item.sources):
            raise ValueError("Summary source belongs to another knowledge scope")
        return self
