"""Store-facing ingest stages: graph, embedding and index writes (ENT-06).

Split out of :mod:`agentic_graphrag.knowledge.ingest_worker` (300-line gate). The
worker keeps the task state machine; these functions do the three writes and
report their own degradation through :func:`record_degradation`, so a stage that
failed is never mistaken for a document that simply had nothing to write.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentic_graphrag.core.enums import RetrievalChannel
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.ports.stores import ChunkRecord, FulltextStore, VectorStore
from agentic_graphrag.knowledge.graph_ingest import GraphIngestor
from agentic_graphrag.observability.degradation import (
    REASON_EMBEDDING_UNAVAILABLE,
    REASON_EXTRACT_FAILED,
    REASON_INDEX_WRITE_FAILED,
    ChannelStatus,
    record_degradation,
)


def ingest_graph(
    ingestor: GraphIngestor | None,
    chunks: list[ChunkRecord],
    *,
    tenant_id: str,
    batch_id: str,
) -> JsonObject | None:
    """Graph stage (BL-01); a failure is reported, never silently dropped."""
    if ingestor is None:
        return None
    try:
        outcome = ingestor.ingest_chunks(chunks, tenant_id=tenant_id, batch_id=batch_id)
    except Exception as exc:  # noqa: BLE001
        record_degradation(
            "graph_ingest", REASON_EXTRACT_FAILED, exc, severity=ChannelStatus.FAILED
        )
        return {"error": f"graph_ingest_failed: {type(exc).__name__}"}
    return outcome.to_dict()


def embed_chunks(embed_fn: Callable[[str], list[float]] | None, chunks: list[Any]) -> str | None:
    """Embed every chunk; return an error when none of them succeeded.

    Per-chunk records alone let a run where *every* embed failed finish as
    ``done`` with zero vectors indexed (docs/design/BUSINESS_LOGIC.md BL-10).
    """
    if embed_fn is None:
        return None
    failures = 0
    for chunk in chunks:
        try:
            chunk.embedding = embed_fn(chunk.text)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            record_degradation(
                RetrievalChannel.EMBEDDING,
                REASON_EMBEDDING_UNAVAILABLE,
                exc,
                chunk_id=chunk.chunk_id,
            )
    if failures and failures == len(chunks):
        return f"embed_failed_all ({failures}/{len(chunks)})"
    return None


def index_chunks(
    chunks: list[Any],
    *,
    doc_id: str,
    vector_store: VectorStore,
    fulltext_store: FulltextStore | None,
) -> int:
    """Upsert embedded chunks; a fulltext failure is recorded but not fatal."""
    embedded = [chunk for chunk in chunks if chunk.embedding]
    indexed = vector_store.upsert(embedded) if embedded else 0
    if fulltext_store is None:
        return indexed
    try:
        fulltext_store.index(chunks)
    except Exception as exc:  # noqa: BLE001
        record_degradation("fulltext_index", REASON_INDEX_WRITE_FAILED, exc, doc_id=doc_id)
    return indexed
