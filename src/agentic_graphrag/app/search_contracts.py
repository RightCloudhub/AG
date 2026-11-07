"""Typed public retrieval and original-evidence response contracts."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_graphrag.app.contracts import CostOut
from agentic_graphrag.core.evidence import Citation
from agentic_graphrag.core.knowledge import SourceRef
from agentic_graphrag.core.knowledge_versions import KnowledgeSnapshot


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    kb_id: str = Field(default="default", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    doc_ids: list[str] | None = Field(default=None, min_length=1, max_length=10000)
    top_k: int = Field(default=10, ge=1, le=50)
    audit_required: bool = False


class SearchCandidate(BaseModel):
    id: str
    content: str
    source: str
    score: float = 0.0
    source_refs: list[SourceRef] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    source_status: str = "unavailable"
    candidate_id: str = ""
    audit_status: Literal["persisted", "failed"] | None = None


class SearchResult(BaseModel):
    search_id: str
    kb_id: str
    candidates: list[SearchCandidate]
    snapshot: KnowledgeSnapshot | None = None
    audit_status: Literal["persisted", "failed", "disabled"]
    cost: CostOut = Field(default_factory=CostOut)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceResult(BaseModel):
    evidence_id: str
    content: str
    source_refs: list[SourceRef]
    status: Literal["available", "unavailable", "deleted", "version_unavailable"]
    offset: int = 0
    total_chars: int = 0
