"""Per-chunk graph extraction for :class:`IndexPipeline` (300-line gate split).

Each chunk's outcome is persisted on the chunk, so a resumed run only re-extracts
chunks whose graph write never completed. Extraction failures are recorded through
:func:`record_degradation` before they become a chunk-level error.
"""

from __future__ import annotations

from agentic_graphrag.core.ports.stores import STAGE_GRAPH, ChunkRecord, DocStore, DocumentRecord
from agentic_graphrag.knowledge.graph_ingest import GraphIngestor
from agentic_graphrag.knowledge.index_result import IngestResult
from agentic_graphrag.knowledge.index_vector_stage import StageContext, stamp_stage
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
    ctx: StageContext,
) -> None:
    result.graph = {"accepted": 0, "conflicts_review": 0}
    for chunk in doc.chunks:
        ctx.heartbeat()
        if chunk.enabled and not chunk.graph_extracted:
            ctx.guard()
            _graph_chunk(doc, chunk, graph=graph, docs=docs, batch_id=batch_id, ctx=ctx)
        outcome = chunk.metadata.get("graph_result", {})
        for key in ("accepted", "conflicts_review"):
            result.graph[key] += int(outcome.get(key, 0))
        result.error = result.error or outcome.get("error")
    if graph.review_queue is not None:
        result.graph["conflicts_review"] = graph.review_queue.count(
            status="pending",
            type="conflict",
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
    ctx: StageContext,
) -> None:
    chunk.metadata["document_version"] = doc.version
    stamped = stamp_stage(chunk, STAGE_GRAPH, generation=ctx.generation)
    try:
        outcome = graph.ingest_chunks([stamped], tenant_id=doc.tenant_id, batch_id=batch_id)
        chunk.metadata["graph_result"] = outcome.to_dict()
        chunk.graph_extracted = not outcome.error
        if chunk.graph_extracted:
            chunk.stages = stamped.stages
    except Exception as exc:
        record_degradation(
            "graph_ingest",
            REASON_EXTRACT_FAILED,
            exc,
            severity=ChannelStatus.FAILED,
            chunk_id=chunk.chunk_id,
        )
        chunk.metadata["graph_result"] = {"error": f"graph_failed: {type(exc).__name__}"}
    ctx.guard()
    docs.save_chunks(doc, [chunk])
