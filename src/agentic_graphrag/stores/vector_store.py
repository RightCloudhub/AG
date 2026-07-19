"""Qdrant VectorStore implementation."""

from __future__ import annotations

from typing import Any
from uuid import uuid5, NAMESPACE_URL

from agentic_graphrag.stores.interfaces import ChunkRecord


class QdrantVectorStore:
    def __init__(self, url: str, collection: str) -> None:
        from qdrant_client import QdrantClient

        self._client = QdrantClient(url=url)
        self._collection = collection
        self._dim: int | None = None

    def ensure_collection(self, dim: int) -> None:
        from qdrant_client.http import models as qm

        self._dim = dim
        names = {c.name for c in self._client.get_collections().collections}
        if self._collection not in names:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
            )

    def upsert(self, chunks: list[ChunkRecord]) -> int:
        from qdrant_client.http import models as qm

        if not chunks:
            return 0
        points = []
        for ch in chunks:
            if not ch.embedding:
                raise ValueError(f"Chunk {ch.chunk_id} missing embedding")
            if self._dim is None:
                self.ensure_collection(len(ch.embedding))
            point_id = str(uuid5(NAMESPACE_URL, ch.chunk_id))
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
                    },
                )
            )
        self._client.upsert(collection_name=self._collection, points=points)
        return len(points)

    def search(self, query_vector: list[float], top_k: int = 10) -> list[tuple[ChunkRecord, float]]:
        hits = self._client.search(
            collection_name=self._collection,
            query_vector=query_vector,
            limit=top_k,
        )
        out: list[tuple[ChunkRecord, float]] = []
        for hit in hits:
            payload: dict[str, Any] = hit.payload or {}
            chunk = ChunkRecord(
                chunk_id=str(payload.get("chunk_id", hit.id)),
                doc_id=str(payload.get("doc_id", "")),
                text=str(payload.get("text", "")),
                index=int(payload.get("index", 0)),
                metadata=payload.get("metadata") or {},
            )
            out.append((chunk, float(hit.score)))
        return out

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
        self._items: list[ChunkRecord] = []
        self._dim: int | None = None

    def ensure_collection(self, dim: int) -> None:
        self._dim = dim

    def upsert(self, chunks: list[ChunkRecord]) -> int:
        for ch in chunks:
            if not ch.embedding:
                raise ValueError(f"Chunk {ch.chunk_id} missing embedding")
            self._items = [x for x in self._items if x.chunk_id != ch.chunk_id]
            self._items.append(ch)
        return len(chunks)

    def search(self, query_vector: list[float], top_k: int = 10) -> list[tuple[ChunkRecord, float]]:
        scored: list[tuple[ChunkRecord, float]] = []
        for ch in self._items:
            if not ch.embedding:
                continue
            score = _cosine(query_vector, ch.embedding)
            scored.append((ch, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def clear(self) -> None:
        self._items.clear()

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
