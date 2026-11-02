"""Qdrant VectorStore implementation."""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.core.tenancy import scoped_id, tenant_matches
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
            point_id = str(uuid5(NAMESPACE_URL, scoped_id(ch.tenant_id, ch.chunk_id)))
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
        out: list[tuple[ChunkRecord, float]] = []
        for hit in hits:
            payload: dict[str, Any] = hit.payload or {}
            chunk = ChunkRecord(
                chunk_id=str(payload.get("chunk_id", hit.id)),
                doc_id=str(payload.get("doc_id", "")),
                text=str(payload.get("text", "")),
                index=int(payload.get("index", 0)),
                metadata=payload.get("metadata") or {},
                tenant_id=str(payload.get("tenant_id") or ""),
                embedding_model=str(payload.get("embedding_model") or ""),
                embedded=True,
            )
            out.append((chunk, float(hit.score)))
        return out

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
    """Simple in-memory cosine store for tests / offline POC without Qdrant."""

    def __init__(self) -> None:
        self.index_generation = uuid4().hex
        self._items: list[ChunkRecord] = []
        self._dim: int | None = None

    def ensure_collection(self, dim: int) -> None:
        self._dim = dim

    def upsert(self, chunks: list[ChunkRecord]) -> int:
        for ch in chunks:
            if not ch.embedding:
                raise ValueError(f"Chunk {ch.chunk_id} missing embedding")
            key = scoped_id(ch.tenant_id, ch.chunk_id)
            self._items = [x for x in self._items if scoped_id(x.tenant_id, x.chunk_id) != key]
            self._items.append(ch)
        return len(chunks)

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        *,
        tenant_id: str | None = None,
    ) -> list[tuple[ChunkRecord, float]]:
        scored: list[tuple[ChunkRecord, float]] = []
        for ch in self._items:
            if not tenant_matches(ch.tenant_id, tenant_id):
                continue
            if not ch.embedding:
                continue
            score = _cosine(query_vector, ch.embedding)
            scored.append((ch, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def delete_by_doc(self, doc_id: str, tenant_id: str) -> int:
        before = len(self._items)
        self._items = [
            c
            for c in self._items
            if not (c.doc_id == doc_id and tenant_matches(c.tenant_id, tenant_id))
        ]
        return before - len(self._items)

    def clear(self) -> None:
        self._items.clear()
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
