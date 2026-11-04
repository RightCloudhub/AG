"""Durable version and change contracts independent of storage adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.core.knowledge import KnowledgeBase, KnowledgeScope


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class KnowledgeSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scope: KnowledgeScope
    generation: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    graph_version: str
    vector_version: str
    summary_version: str = ""
    schema_version: str = ""
    model_config_revision: str = ""
    document_selection_fingerprint: str = ""
    payload_sha256: str = ""
    knowledge_base: KnowledgeBase | None = None
    created_at: str = Field(default_factory=timestamp)


class WriteIntent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    operation_id: str = Field(min_length=1, max_length=200)
    scope: KnowledgeScope
    source_generation: str
    target_generation: str
    document_ids: tuple[str, ...] = ()
    expected_revision: int = Field(default=1, ge=1)
    stage: Literal["pending", "seeded"] = "pending"
    index_generations: tuple[tuple[str, str], ...] = ()
    status: Literal["pending", "completed"] = "pending"
    created_at: str = Field(default_factory=timestamp)


class GraphChangeSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    operation_id: str = Field(min_length=1, max_length=200)
    scope: KnowledgeScope
    sequence: int = Field(default=0, ge=0)
    source_generation: str
    target_generation: str
    entity_upserts: tuple[str, ...] = ()
    entity_deletes: tuple[str, ...] = ()
    relation_upserts: tuple[str, ...] = ()
    relation_deletes: tuple[str, ...] = ()
    source_retractions: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()
    topology_changed: bool = True
    created_at: str = Field(default_factory=timestamp)
