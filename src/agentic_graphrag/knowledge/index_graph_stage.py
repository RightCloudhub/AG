"""Per-chunk graph extraction for :class:`IndexPipeline` (300-line gate split).

Each chunk's outcome is persisted on the chunk, so a resumed run only re-extracts
chunks whose graph write never completed. Extraction failures are recorded through
:func:`record_degradation` before they become a chunk-level error.
"""

from __future__ import annotations

from collections.abc import Callable

from agentic_graphrag.core.ports.stores import ChunkRecord, DocStore, DocumentRecord
from agentic_graphrag.knowledge.graph_ingest import GraphIngestor
from agentic_graphrag.knowledge.index_result import IngestResult
from agentic_graphrag.observability.degradation import (
    REASON_EXTRACT_FAILED,
    ChannelStatus,
    record_degradation,
)


def run_graph_stage(
    doc: DocumentRecord,
    result: IngestResult,
    *,
    graph: GraphIngestor,
    docs: DocStore,
    batch_id: str,
    heartbeat: Callable[[], None],
) -> None:
    result.graph = {"accepted": 0, "conflicts_review": 0}
    for chunk in doc.chunks:
        heartbeat()
        if not chunk.graph_extracted:
            _graph_chunk(doc, chunk, graph=graph, docs=docs, batch_id=batch_id)
        outcome = chunk.metadata.get("graph_result", {})
        for key in ("accepted", "conflicts_review"):
            result.graph[key] += int(outcome.get(key, 0))
        result.error = result.error or outcome.get("error")
    if graph.review_queue is not None:
        result.graph["conflicts_review"] = graph.review_queue.count(
            status="pending",
            tenant_id=doc.tenant_id,
            batch_id=batch_id or None,
            doc_id=doc.doc_id,
            doc_version=doc.version,
        )


def _graph_chunk(
    doc: DocumentRecord,
    chunk: ChunkRecord,
    *,
    graph: GraphIngestor,
    docs: DocStore,
    batch_id: str,
) -> None:
    chunk.metadata["document_version"] = doc.version
    try:
        outcome = graph.ingest_chunks([chunk], tenant_id=doc.tenant_id, batch_id=batch_id)
        chunk.metadata["graph_result"] = outcome.to_dict()
        chunk.graph_extracted = not outcome.error
    except Exception as exc:
        record_degradation(
            "graph_ingest",
            REASON_EXTRACT_FAILED,
            exc,
            severity=ChannelStatus.FAILED,
            chunk_id=chunk.chunk_id,
        )
        chunk.metadata["graph_result"] = {"error": f"graph_failed: {type(exc).__name__}"}
    docs.save_chunks(doc, [chunk])
