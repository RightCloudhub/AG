"""Storage abstractions — Repository pattern (NFR-10 / P2-ARCH-02).

All graph / vector / fulltext / document backends MUST implement these
``Protocol``s. Application code depends only on the protocols; concrete
adapters live in sibling modules (``memory_graph``, ``neo4j_store``, …).

``isinstance(obj, GraphStore)`` works at runtime via ``@runtime_checkable``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class EntityRecord:
    id: str
    name: str
    type: str
    attributes: dict[str, Any] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RelationRecord:
    id: str
    type: str
    head_id: str
    tail_id: str
    head_name: str = ""
    tail_name: str = ""
    confidence: float = 1.0
    attributes: dict[str, Any] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PathRecord:
    nodes: list[EntityRecord]
    relations: list[RelationRecord]
    length: int
    score: float = 0.0


@dataclass
class DocumentRecord:
    doc_id: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChunkRecord:
    chunk_id: str
    doc_id: str
    text: str
    index: int
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None


@runtime_checkable
class GraphStore(Protocol):
    """Graph repository: entities, relations, multi-hop neighbors / paths."""

    def clear(self) -> None: ...

    def upsert_entities(self, entities: list[EntityRecord]) -> int: ...

    def upsert_relations(self, relations: list[RelationRecord]) -> int: ...

    def get_entity_by_name(
        self, name: str, entity_type: str | None = None
    ) -> EntityRecord | None: ...

    def neighbors(
        self,
        entity_name: str,
        *,
        max_hops: int = 1,
        relation_types: list[str] | None = None,
        limit: int = 50,
    ) -> list[tuple[RelationRecord, EntityRecord]]: ...

    def paths(
        self,
        source_name: str,
        target_name: str,
        *,
        max_hops: int = 4,
        limit: int = 20,
    ) -> list[PathRecord]: ...

    def counts(self) -> dict[str, int]: ...

    def close(self) -> None: ...


@runtime_checkable
class VectorStore(Protocol):
    """Vector similarity store for chunk embeddings."""

    def ensure_collection(self, dim: int) -> None: ...

    def upsert(self, chunks: list[ChunkRecord]) -> int: ...

    def search(
        self, query_vector: list[float], top_k: int = 10
    ) -> list[tuple[ChunkRecord, float]]: ...

    def clear(self) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class FulltextStore(Protocol):
    """Keyword / BM25 fulltext index over chunks."""

    def index(self, chunks: list[ChunkRecord]) -> int: ...

    def search(self, query: str, top_k: int = 10) -> list[tuple[ChunkRecord, float]]: ...

    def clear(self) -> None: ...


@runtime_checkable
class DocStore(Protocol):
    """Document blob store (source of truth for raw document text)."""

    def save(self, doc: DocumentRecord) -> None: ...

    def get(self, doc_id: str) -> DocumentRecord | None: ...

    def list_ids(self) -> list[str]: ...
