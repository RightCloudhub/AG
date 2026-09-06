"""Runtime graph ingestion path (BL-01): uploaded docs → triples → graph.

``POST /v1/docs`` used to reach only the vector / fulltext indexes: no runtime
code path carried extracted entities and relations into the graph store, so
graph retrieval — the product's core multi-hop value — never saw uploaded
knowledge (docs/BUSINESS_LOGIC.md BL-01).

This pipeline is worker-driven: chunk → LLM extract → incremental apply
(schema + confidence gate → conflict detect → write). Low-confidence conflicts
land in the review queue, where ``ReviewExecutor`` turns human decisions into
graph writes (BL-03). Without a live LLM the task is routed to REVIEW instead
of silently skipping the graph — the honest failure mode, and it gives
``IngestStatus.REVIEW`` its first producer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from agentic_graphrag.knowledge.extraction import extract_from_chunk
from agentic_graphrag.knowledge.incremental import BatchResult, IncrementalUpdater
from agentic_graphrag.knowledge.ingest import chunk_document
from agentic_graphrag.knowledge.schema_check import (
    Triple,
    default_confidence_threshold,
    default_schema,
)
from agentic_graphrag.llm.provider import LLMProvider
from agentic_graphrag.stores.interfaces import DocumentRecord

logger = logging.getLogger(__name__)

# Error marker: no live LLM is configured, so triple extraction is impossible.
LLM_UNAVAILABLE = "llm_unavailable"


@dataclass
class GraphIngestResult:
    doc_id: str
    chunks_considered: int = 0
    triples_extracted: int = 0
    triples_accepted: int = 0
    triples_rejected: int = 0
    conflicts_auto: int = 0
    conflicts_review: int = 0
    conflicts_kept: int = 0
    review_item_ids: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "chunks_considered": self.chunks_considered,
            "triples_extracted": self.triples_extracted,
            "triples_accepted": self.triples_accepted,
            "triples_rejected": self.triples_rejected,
            "conflicts_auto": self.conflicts_auto,
            "conflicts_review": self.conflicts_review,
            "conflicts_kept": self.conflicts_kept,
            "review_item_ids": self.review_item_ids,
            "error": self.error,
        }


class GraphIngestPipeline:
    """Extract triples from one document and apply them to the graph store.

    Extraction is gated per chunk (schema + confidence); the incremental batch
    applies a second gate plus conflict detection, so every triple that reaches
    the graph passed both.
    """

    def __init__(
        self,
        *,
        updater: IncrementalUpdater,
        llm: LLMProvider | None = None,
        chunk_size_chars: int = 1200,
        chunk_overlap_chars: int = 150,
    ) -> None:
        self.updater = updater
        self.llm = llm
        self.chunk_size_chars = chunk_size_chars
        self.chunk_overlap_chars = chunk_overlap_chars

    def process_document(self, doc: DocumentRecord) -> GraphIngestResult:
        if self.llm is None:
            # Honest refusal beats a silent skip: the worker routes the task to
            # REVIEW rather than pretending the graph was updated.
            return GraphIngestResult(doc_id=doc.doc_id, error=LLM_UNAVAILABLE)
        result = GraphIngestResult(doc_id=doc.doc_id)
        try:
            chunks = chunk_document(
                doc,
                chunk_size=self.chunk_size_chars,
                overlap=self.chunk_overlap_chars,
            )
        except Exception as exc:  # noqa: BLE001
            result.error = f"chunking_failed: {type(exc).__name__}"
            return result
        result.chunks_considered = len(chunks)
        triples: list[Triple] = []
        for chunk in chunks:
            try:
                accepted, _rejected = extract_from_chunk(
                    chunk,
                    default_schema(),
                    self.llm,
                    confidence_threshold=default_confidence_threshold(),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Extraction failed for %s: %s", chunk.chunk_id, exc)
                continue
            result.triples_extracted += len(accepted)
            triples.extend(accepted)
        if not triples:
            result.error = "extraction_failed_all"
            return result
        batch = self.updater.apply_batch(triples, tenant_id=doc.tenant_id)
        self._merge_batch(result, batch)
        return result

    def _merge_batch(self, result: GraphIngestResult, batch: BatchResult) -> None:
        result.triples_accepted = batch.accepted
        result.triples_rejected = batch.rejected
        result.conflicts_auto = batch.conflicts_auto
        result.conflicts_review = batch.conflicts_review
        result.conflicts_kept = batch.conflicts_kept
        result.review_item_ids = list(batch.review_item_ids)
