"""Qdrant payload codec and revision-scoped filters for chunk points (K-05)."""

from __future__ import annotations

from typing import Any

from agentic_graphrag.core.ports.stores import STAGE_VECTOR, ChunkIdentity, ChunkKey, ChunkRecord
from agentic_graphrag.stores.qdrant_models import tenant_filter


def identity_payload(identity: ChunkIdentity) -> dict[str, int]:
    return {
        "doc_version": identity.doc_version,
        "revision": identity.revision,
        "generation": identity.generation,
    }


def chunk_from_payload(payload: dict[str, Any], point_id: Any) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=str(payload.get("chunk_id", point_id)),
        doc_id=str(payload.get("doc_id", "")),
        text=str(payload.get("text", "")),
        index=int(payload.get("index", 0)),
        metadata=payload.get("metadata") or {},
        tenant_id=str(payload.get("tenant_id") or ""),
        embedding_model=str(payload.get("embedding_model") or ""),
        embedded=True,
        revision=int(payload.get("revision") or 1),
        stages={STAGE_VECTOR: {"generation": int(payload.get("generation") or 0)}},
    )


def newest_per_chunk(hits: list[tuple[ChunkRecord, float]]) -> list[tuple[ChunkRecord, float]]:
    """Keep only the highest-identity point of each logical chunk, in rank order."""
    best: dict[tuple[str, str], ChunkIdentity] = {}
    for chunk, _ in hits:
        key = (chunk.tenant_id or "default", chunk.chunk_id)
        identity = chunk.identity(STAGE_VECTOR)
        if key not in best or identity > best[key]:
            best[key] = identity
    return [
        (chunk, score)
        for chunk, score in hits
        if best[(chunk.tenant_id or "default", chunk.chunk_id)] == chunk.identity(STAGE_VECTOR)
    ]


def _at_least(below: ChunkIdentity) -> Any:
    """Filter matching points whose identity is >= ``below`` (lexicographic)."""
    from qdrant_client.http import models as qm

    def eq(name: str, value: int) -> Any:
        return qm.FieldCondition(key=name, match=qm.MatchValue(value=value))

    def rng(name: str, **bounds: int) -> Any:
        return qm.FieldCondition(key=name, range=qm.Range(**bounds))

    return qm.Filter(
        should=[
            rng("doc_version", gt=below.doc_version),
            qm.Filter(
                must=[eq("doc_version", below.doc_version), rng("revision", gt=below.revision)]
            ),
            qm.Filter(
                must=[
                    eq("doc_version", below.doc_version),
                    eq("revision", below.revision),
                    rng("generation", gte=below.generation),
                ]
            ),
        ]
    )


def chunk_scope(key: ChunkKey, below: ChunkIdentity | None) -> Any:
    from qdrant_client.http import models as qm

    must: list[Any] = [
        tenant_filter(key.tenant_id),
        qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=key.doc_id)),
        qm.FieldCondition(key="chunk_id", match=qm.MatchValue(value=key.chunk_id)),
    ]
    return qm.Filter(must=must, must_not=[_at_least(below)] if below is not None else None)
