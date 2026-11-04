"""Evidence, snapshots and audit views retain source/version lineage."""

from typing import Any, Literal

from pydantic import Field

from ag_client.models import AuditStatus, Cost, QueryResult, WireModel


class KnowledgeScope(WireModel):
    tenant_id: str = "default"
    kb_id: str = "default"


class SourceRef(WireModel):
    scope: KnowledgeScope = Field(default_factory=KnowledgeScope)
    doc_id: str
    doc_version: int
    chunk_id: str = ""
    content_sha256: str = ""
    span_origin: Literal["document", "chunk"] = "document"
    span_start: int = 0
    span_end: int | None = None
    page: int | None = None
    paragraph: str | None = None


class Snapshot(WireModel):
    scope: KnowledgeScope = Field(default_factory=KnowledgeScope)
    generation: str | int = 1
    graph_version: str | int = 0
    vector_version: str | int = 0
    summary_version: str | int = 0


class Citation(WireModel):
    doc_id: str | None = None
    chunk_id: str | None = None
    entity_id: str | None = None
    relation_id: str | None = None
    span: str | None = None


class Candidate(WireModel):
    id: str
    content: str
    source: str = ""
    score: float = 0
    structured: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    citations: list[Citation] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)


class SearchResult(WireModel):
    search_id: str
    kb_id: str = "default"
    candidates: list[Candidate]
    snapshot: Snapshot | None = None
    audit_status: AuditStatus
    cost: Cost = Field(default_factory=Cost)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceResult(WireModel):
    evidence_id: str
    content: str = ""
    status: Literal["available", "unavailable", "deleted", "version_unavailable"] = "available"
    source_refs: list[SourceRef] = Field(default_factory=list)
    offset: int = 0
    total_chars: int = 0

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.content) < self.total_chars


class QueryAudit(QueryResult):
    """Existing query audit is the complete reasoning-chain shape."""


class SearchAudit(SearchResult):
    """Search audits never masquerade as generated answer chains."""

    query: str = ""
    kind: Literal["search"] = "search"
