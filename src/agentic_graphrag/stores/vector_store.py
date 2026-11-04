"""Qdrant VectorStore implementation."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid4, uuid5

from agentic_graphrag.core.ports.stores import STAGE_VECTOR, ChunkIdentity, ChunkKey, ChunkRecord
from agentic_graphrag.core.tenancy import scoped_id, tenant_matches
from agentic_graphrag.stores.chunk_identity import admits, remove_below
from agentic_graphrag.stores.qdrant_chunks import (
    chunk_from_payload,
    chunk_scope,
    identity_payload,
    newest_per_chunk,
)
from agentic_graphrag.stores.qdrant_models import QdrantModelRouting, tenant_filter


class QdrantVectorStore(QdrantModelRouting):
    def __init__(self, url: str, collection: str) -> None:
        from qdrant_client import QdrantClient

        self._client = QdrantClient(url=url)
        self._collection = collection
        self._routing_enabled = True
        self._dim: int | None = None

    def ping(self) -> None:
        """Raise if Qdrant is unreachable (used by the factory to fail fast)."""
        self._client.get_collections()

    def ensure_collection(self, dim: int, *, collection: str | None = None) -> None:
        from qdrant_client.http import models as qm

        collection = collection or self._collection
        self._dim = dim
        names = {c.name for c in self._client.get_collections().collections}
        if collection not in names:
            self._client.create_collection(
                collection_name=collection,
                vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
            )

    def upsert(self, chunks: list[ChunkRecord]) -> int:
        return sum(
            self._upsert_collection(group, collection=collection)
            for collection, group in self._group_chunks(chunks).items()
        )

    def _upsert_collection(self, chunks: list[ChunkRecord], *, collection: str) -> int:
        from qdrant_client.http import models as qm

        if not chunks:
            return 0
        self.ensure_collection(len(chunks[0].embedding or []), collection=collection)
        points = []
        for ch in chunks:
            if not ch.embedding:
                raise ValueError(f"Chunk {ch.chunk_id} missing embedding")
            ident = ch.identity(STAGE_VECTOR)
            # Physical point per (tenant, chunk, version, revision, generation): an
            # obsolete writer creates a lower-identity point instead of overwriting.
            physical = scoped_id(ch.tenant_id, ident.physical_key(ch.chunk_id))
            point_id = str(uuid5(NAMESPACE_URL, physical))
            points.append(
                qm.PointStruct(
                    id=point_id,
                    vector=ch.embedding,
                    payload={
                        "chunk_id": ch.chunk_id,
                        "doc_id": ch.doc_id,
                        "text": ch.text,
                        "index": ch.index,
                        "metadata": ch.metadata or {},
                        "tenant_id": ch.tenant_id,
                        "embedding_model": ch.embedding_model,
                        **identity_payload(ident),
                    },
                )
            )
        self._client.upsert(collection_name=collection, points=points, wait=True)
        return len(points)

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        *,
        tenant_id: str | None = None,
    ) -> list[tuple[ChunkRecord, float]]:
        return self._search_collection(
            query_vector,
            top_k=top_k,
            tenant_id=tenant_id,
            collection=self._active_collection(tenant_id),
        )

    def _search_collection(
        self, query_vector: list[float], *, top_k: int, tenant_id: str | None, collection: str
    ) -> list[tuple[ChunkRecord, float]]:
        query_filter = None
        if tenant_id is not None:
            from qdrant_client.http import models as qm

            query_filter = qm.Filter(
                must=[qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id))]
            )
            if tenant_id in {"", "default"}:
                query_filter = qm.Filter(
                    should=[
                        qm.FieldCondition(key="tenant_id", match=qm.MatchAny(any=["default", ""])),
                        qm.IsEmptyCondition(is_empty=qm.PayloadField(key="tenant_id")),
                    ]
                )
        hits = self._client.query_points(
            collection_name=collection,
            query=query_vector,
            query_filter=query_filter,
            limit=top_k,
        ).points
        return newest_per_chunk(
            [(chunk_from_payload(hit.payload or {}, hit.id), float(hit.score)) for hit in hits]
        )

    def delete_by_doc(self, doc_id: str, tenant_id: str) -> int:
        from qdrant_client.http import models as qm

        scope = qm.Filter(
            must=[
                tenant_filter(tenant_id),
                qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
            ]
        )
        count = 0
        for name in self._document_collections(tenant_id):
            count += self._client.count(collection_name=name, count_filter=scope).count
            self._client.delete(
                collection_name=name, points_selector=qm.FilterSelector(filter=scope), wait=True
            )
        return int(count)

    def delete_chunk(self, key: ChunkKey, *, below: ChunkIdentity | None = None) -> int:
        """Delete the chunk's points older than ``below`` (all when ``None``)."""
        from qdrant_client.http import models as qm

        scope = chunk_scope(key, below)
        count = 0
        for name in self._document_collections(key.tenant_id):
            count += self._client.count(collection_name=name, count_filter=scope).count
            self._client.delete(
                collection_name=name, points_selector=qm.FilterSelector(filter=scope), wait=True
            )
        return int(count)

    def clear(self) -> None:
        from qdrant_client.http import models as qm

        names = {c.name for c in self._client.get_collections().collections}
        if self._collection in names:
            self._client.delete(
                collection_name=self._collection,
                points_selector=qm.FilterSelector(filter=qm.Filter(must=[])),
            )

    def close(self) -> None:
        self._client.close()


class InMemoryVectorStore:
    """Simple in-memory cosine store for tests / offline POC without Qdrant.

    One record per logical chunk; a write with a lower :class:`ChunkIdentity` than
    the stored one (or below a cleanup floor) is ignored, so late writes from an
    obsolete worker never replace newer content.
    """

    def __init__(self) -> None:
        self.index_generation = uuid4().hex
        self._items: dict[str, ChunkRecord] = {}
        self._floors: dict[str, ChunkIdentity] = {}
        self._dim: int | None = None

    def ensure_collection(self, dim: int) -> None:
        self._dim = dim

    def upsert(self, chunks: list[ChunkRecord]) -> int:
        for ch in chunks:
            if not ch.embedding:
                raise ValueError(f"Chunk {ch.chunk_id} missing embedding")
        for ch in chunks:
            key = scoped_id(ch.tenant_id, ch.chunk_id)
            if admits(self._items, self._floors, (key, ch), stage=STAGE_VECTOR):
                self._items[key] = ch
        return len(chunks)

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        *,
        tenant_id: str | None = None,
    ) -> list[tuple[ChunkRecord, float]]:
        scored: list[tuple[ChunkRecord, float]] = []
        for ch in self._items.values():
            if not tenant_matches(ch.tenant_id, tenant_id):
                continue
            if not ch.embedding:
                continue
            score = _cosine(query_vector, ch.embedding)
            scored.append((ch, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def delete_by_doc(self, doc_id: str, tenant_id: str) -> int:
        doomed = [
            key
            for key, c in self._items.items()
            if c.doc_id == doc_id and tenant_matches(c.tenant_id, tenant_id)
        ]
        for key in doomed:
            del self._items[key]
        return len(doomed)

    def delete_chunk(self, key: ChunkKey, *, below: ChunkIdentity | None = None) -> int:
        scoped = scoped_id(key.tenant_id, key.chunk_id)
        return remove_below(self._items, self._floors, scoped, below=below, stage=STAGE_VECTOR)

    def clear(self) -> None:
        self._items.clear()
        self._floors.clear()
        self.index_generation = uuid4().hex

    def close(self) -> None:
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
