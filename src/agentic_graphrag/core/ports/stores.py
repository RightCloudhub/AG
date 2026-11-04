"""Storage abstractions — Repository pattern (NFR-10 / P2-ARCH-02).

All graph / vector / fulltext / document backends MUST implement these
``Protocol``s. Application code depends only on the protocols; concrete
adapters live in sibling modules (``memory_graph``, ``neo4j_store``, …).

``isinstance(obj, GraphStore)`` works at runtime via ``@runtime_checkable``.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class RelationDeleter(Protocol):
    def __call__(self, relation_id: str, *, tenant_id: str | None = None) -> bool: ...


@dataclass
class EntityRecord:
    id: str
    name: str
    type: str
    attributes: dict[str, Any] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    tenant_id: str = ""


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
    tenant_id: str = ""


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
    tenant_id: str = ""

    source_uri: str = ""
    content_sha256: str = ""
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    chunks: list[ChunkRecord] = field(default_factory=list)


@dataclass
class ChunkRecord:
    chunk_id: str
    doc_id: str
    text: str
    index: int
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None
    tenant_id: str = ""

    embedded: bool = False
    fulltext_indexed: bool = False
    graph_extracted: bool = False
    embedding_model: str = ""
    # K-04: legacy payloads decode as enabled revision 1. ``stages`` keeps per-stage
    # {state, revision, generation, error, model} so physical index identities are
    # revision- and generation-scoped (see :class:`ChunkIdentity`).
    enabled: bool = True
    revision: int = 1
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)

    def identity(self, stage: str) -> ChunkIdentity:
        """Physical identity of this chunk's record in one index stage."""
        generation = int((self.stages.get(stage) or {}).get("generation") or 0)
        version = int(self.metadata.get("document_version") or 0)
        return ChunkIdentity(version, int(self.revision), generation)


STAGE_VECTOR = "vector"
STAGE_FULLTEXT = "fulltext"
STAGE_GRAPH = "graph"
INDEX_STAGES = (STAGE_VECTOR, STAGE_FULLTEXT, STAGE_GRAPH)


@dataclass(frozen=True, order=True)
class ChunkIdentity:
    """Ordered physical identity: document version, chunk revision, stage generation.

    Index adapters never let a lower identity overwrite or delete a higher one, so a
    late write or cleanup from an obsolete worker cannot clobber newer content.
    """

    doc_version: int = 0
    revision: int = 0
    generation: int = 0

    def physical_key(self, chunk_id: str) -> str:
        return f"{chunk_id}@{self.doc_version}.{self.revision}.{self.generation}"

    @classmethod
    def of_source(cls, source: dict[str, Any]) -> ChunkIdentity:
        """Identity recorded on a graph provenance entry (legacy entries are lowest)."""
        return cls(
            int(source.get("doc_version") or 0),
            int(source.get("revision") or 0),
            int(source.get("generation") or 0),
        )


@dataclass(frozen=True)
class ChunkKey:
    """Logical chunk address within a tenant."""

    tenant_id: str
    doc_id: str
    chunk_id: str


@runtime_checkable
class GraphStore(Protocol):
    """Graph repository: entities, relations, multi-hop neighbors / paths."""

    def delete_by_doc(self, doc_id: str, tenant_id: str) -> int: ...

    def clear(self) -> None: ...

    def upsert_entities(self, entities: list[EntityRecord]) -> int: ...

    def upsert_relations(self, relations: list[RelationRecord]) -> int: ...

    def get_entity_by_name(
        self, name: str, entity_type: str | None = None, *, tenant_id: str | None = None
    ) -> EntityRecord | None: ...

    def neighbors(
        self,
        entity_name: str,
        *,
        max_hops: int = 1,
        relation_types: list[str] | None = None,
        limit: int = 50,
        tenant_id: str | None = None,
    ) -> list[tuple[RelationRecord, EntityRecord]]: ...

    def paths(
        self,
        source_name: str,
        target_name: str,
        *,
        max_hops: int = 4,
        limit: int = 20,
        tenant_id: str | None = None,
    ) -> list[PathRecord]: ...

    def counts(self) -> dict[str, int]: ...

    def list_entities(
        self, *, limit: int = 50, offset: int = 0, tenant_id: str | None = None
    ) -> list[EntityRecord]: ...

    def close(self) -> None: ...


@runtime_checkable
class VectorStore(Protocol):
    """Vector similarity store for chunk embeddings."""

    def ensure_collection(self, dim: int) -> None: ...

    def upsert(self, chunks: list[ChunkRecord]) -> int: ...

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        *,
        tenant_id: str | None = None,
    ) -> list[tuple[ChunkRecord, float]]: ...

    def delete_by_doc(self, doc_id: str, tenant_id: str) -> int: ...

    def clear(self) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class FulltextStore(Protocol):
    """Keyword / BM25 fulltext index over chunks."""

    def index(self, chunks: list[ChunkRecord]) -> int: ...

    def search(
        self, query: str, top_k: int = 10, *, tenant_id: str | None = None
    ) -> list[tuple[ChunkRecord, float]]: ...

    def delete_by_doc(self, doc_id: str, tenant_id: str) -> int: ...

    def clear(self) -> None: ...


@runtime_checkable
class ChunkScopedIndex(Protocol):
    """Vector/fulltext capability: remove one logical chunk's obsolete records.

    ``below=None`` removes every physical record of the chunk; otherwise only
    records whose identity is strictly lower than ``below`` are removed.
    """

    def delete_chunk(self, key: ChunkKey, *, below: ChunkIdentity | None = None) -> int: ...


@runtime_checkable
class ChunkScopedGraph(Protocol):
    """Graph capability: drop one chunk's provenance; facts supported elsewhere survive."""

    def delete_chunk_support(self, key: ChunkKey, *, below: ChunkIdentity | None = None) -> int: ...


@runtime_checkable
class DocStore(Protocol):
    """Document blob store (source of truth for raw document text)."""

    # False for process-local stores: writes are invisible to any other process,
    # so a separately-run ingest worker cannot see documents the API accepted.
    durable: bool

    def save(self, doc: DocumentRecord) -> None: ...

    def save_chunks(self, doc: DocumentRecord, chunks: list[ChunkRecord]) -> None:
        """Checkpoint only these chunks; reject missing documents or obsolete versions.

        Chunks whose stored revision is newer than the given one are skipped, so a
        worker holding an old snapshot cannot overwrite an edit or a disable.
        """
        ...

    def get(self, doc_id: str, *, tenant_id: str | None = None) -> DocumentRecord | None: ...

    def list_ids(self, *, tenant_id: str | None = None) -> list[str]: ...

    def list_refs(self, *, tenant_id: str | None = None) -> list[tuple[str, str]]: ...

    def delete(self, doc_id: str, *, tenant_id: str) -> None: ...

    def find(
        self, value: str, *, tenant_id: str, by_hash: bool = False
    ) -> DocumentRecord | None: ...

    def lock(self) -> AbstractContextManager[None]: ...
