"""Persistent per-chunk stages; successful writes precede progress checkpoints.

With a lease-capable doc store, all stages of one document run under a single
document lease whose fencing token is checked before every checkpoint and used as
the stage generation of physical index identities (release 5a, K-05/K-06).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from agentic_graphrag.core.ports.document_ops import DocumentBusy, Fence
from agentic_graphrag.core.ports.document_ops_store import LeaseStore
from agentic_graphrag.core.ports.stores import (
    STAGE_FULLTEXT,
    ChunkRecord,
    DocStore,
    DocumentRecord,
    FulltextStore,
    VectorStore,
)
from agentic_graphrag.knowledge.chunk_splitting import ChunkingError
from agentic_graphrag.knowledge.document_lifecycle import DocumentLifecycle
from agentic_graphrag.knowledge.embedding_batch import BatchEmbedder
from agentic_graphrag.knowledge.graph_ingest import GraphIngestor
from agentic_graphrag.knowledge.index_config import IngestWorkerConfig
from agentic_graphrag.knowledge.index_graph_stage import run_graph_stage
from agentic_graphrag.knowledge.index_recovery import invalidate_stages, stale_stages
from agentic_graphrag.knowledge.index_result import IngestResult
from agentic_graphrag.knowledge.index_vector_stage import StageContext, VectorStage, stamp_stage
from agentic_graphrag.knowledge.ingest import chunk_document
from agentic_graphrag.knowledge.ingest_tasks import LeaseLost
from agentic_graphrag.observability.degradation import REASON_INDEX_WRITE_FAILED, record_degradation

DOCUMENT_BUSY = "document_busy"


class IndexPipeline:
    def __init__(
        self,
        docs: DocStore,
        *,
        vector: VectorStore,
        fulltext: FulltextStore | None,
        graph: GraphIngestor | None,
        embed_many: BatchEmbedder | None,
        config: IngestWorkerConfig,
        on_commit: Callable[[], None] | None = None,
    ) -> None:
        self.docs, self.vector, self.fulltext, self.graph = docs, vector, fulltext, graph
        self.embed_many, self.config, self.on_commit = embed_many, config, on_commit
        self.owner = f"pipeline-{uuid.uuid4().hex[:8]}"
        self.lifecycle = DocumentLifecycle(
            docs, vector=vector, fulltext=fulltext, graph=graph.graph if graph else None
        )

    def _indexes(self, *, reindex: bool = False) -> dict[str, object]:
        indexes: dict[str, object] = {"embedded": self.vector}
        if not reindex:
            indexes.update(
                fulltext_indexed=self.fulltext,
                graph_extracted=self.graph.graph if self.graph else None,
            )
        return indexes

    def needs_processing(self, doc: DocumentRecord) -> bool:
        return bool(
            doc.metadata.get("lifecycle")
            or not doc.metadata.get("index_complete")
            or stale_stages(doc, self._indexes())
        )

    def process(
        self,
        doc_id: str,
        *,
        tenant_id: str | None,
        batch_id: str = "",
        heartbeat: Callable[[], None] = lambda: None,
        reindex: bool = False,
    ) -> IngestResult:
        """Index one document; raises :class:`DocumentBusy` while another worker holds it."""
        with self.docs.lock():
            fence = self.acquire(tenant_id or "default", doc_id)
            try:
                ctx = self.context(fence, heartbeat=heartbeat, reindex=reindex)
                return self._process(doc_id, tenant_id=tenant_id, batch_id=batch_id, ctx=ctx)
            finally:
                self.release(fence)

    def process_or_busy(
        self, doc_id: str, *, tenant_id: str | None, reindex: bool = False
    ) -> IngestResult:
        """Like :meth:`process`, but a held document lease is a non-fatal result."""
        try:
            return self.process(doc_id, tenant_id=tenant_id, reindex=reindex)
        except DocumentBusy:
            return IngestResult(doc_id, error=DOCUMENT_BUSY)

    def acquire(self, tenant_id: str, doc_id: str) -> Fence | None:
        if not isinstance(self.docs, LeaseStore):
            return None
        fence = self.docs.acquire_lease(
            (tenant_id, doc_id), owner=self.owner, ttl=self.config.stale_task_seconds
        )
        if fence is None:
            raise DocumentBusy("Another worker holds the document lease")
        return fence

    def release(self, fence: Fence | None) -> None:
        if fence is not None and isinstance(self.docs, LeaseStore):
            self.docs.release_lease(fence)

    def context(
        self, fence: Fence | None, *, heartbeat: Callable[[], None], reindex: bool
    ) -> StageContext:
        def guard() -> None:
            if fence is not None and isinstance(self.docs, LeaseStore):
                if not self.docs.renew_lease(fence, ttl=self.config.stale_task_seconds):
                    raise LeaseLost("Document lease lost")

        def beat() -> None:
            heartbeat()
            guard()

        generation = fence.token if fence else 0
        return StageContext(beat, generation=generation, reindex=reindex, guard=guard)

    def _process(
        self, doc_id: str, *, tenant_id: str | None, batch_id: str, ctx: StageContext
    ) -> IngestResult:
        doc = self.docs.get(doc_id, tenant_id=tenant_id)
        if doc is None:
            return IngestResult(doc_id, error="not_found", fatal=True)
        doc = self.lifecycle.resume(doc)
        if doc is None:
            return IngestResult(doc_id)
        generations = stale_stages(doc, self._indexes(reindex=ctx.reindex))
        if generations:
            invalidate_stages(doc, generations)
            self.docs.save(doc)
        chunk_error = self._ensure_chunks(doc)
        if chunk_error:
            return IngestResult(doc_id, error=chunk_error, fatal=True, unchunkable=True)
        result = IngestResult(doc_id, chunks_created=len(doc.chunks))
        if self.embed_many is not None:
            VectorStage(self.docs, self.vector, embed_many=self.embed_many, config=self.config).run(
                doc, result, ctx
            )
        if not ctx.reindex:
            self._fulltext(doc, result, ctx)
            self._graph(doc, result, batch_id=batch_id, ctx=ctx)
        return self._finish(doc, result)

    def _finish(self, doc: DocumentRecord, result: IngestResult) -> IngestResult:
        result.counts = self.counts(doc)
        result.chunks_indexed = result.counts["embedded"]
        doc.metadata["index_complete"] = result.counts["incomplete"] == 0
        doc.metadata["index_error"] = result.error
        self.docs.save(doc)
        if isinstance(self.docs, LeaseStore):
            self.docs.bump_generation(doc.tenant_id or "default")
        if self.on_commit:
            self.on_commit()
        return result

    def _ensure_chunks(self, doc: DocumentRecord) -> str | None:
        """Chunk once per version; returns the terminal chunking error, if any."""
        if doc.metadata.get("chunks_initialized"):
            return None
        try:
            doc.chunks = chunk_document(
                doc,
                chunk_size=self.config.chunk_size_tokens,
                overlap=self.config.chunk_overlap_tokens,
                tokenizer=self.config.tokenizer,
            )
        except ChunkingError as exc:
            doc.metadata["index_error"] = str(exc)
            self.docs.save(doc)
            return str(exc)
        doc.metadata["chunks_initialized"] = True
        self.docs.save(doc)
        return None

    def counts(self, doc: DocumentRecord) -> dict[str, int]:
        return {
            "total": len(doc.chunks),
            "embedded": sum(c.embedded for c in doc.chunks),
            "fulltext_indexed": sum(c.fulltext_indexed for c in doc.chunks),
            "graph_extracted": sum(c.graph_extracted for c in doc.chunks),
            "incomplete": sum(not self.complete(c) for c in doc.chunks),
        }

    def complete(self, chunk: ChunkRecord) -> bool:
        """Disabled chunks have nothing to index; they never hold a task open."""
        return not chunk.enabled or (
            (self.embed_many is None or chunk.embedded)
            and (self.fulltext is None or chunk.fulltext_indexed)
            and (self.graph is None or chunk.graph_extracted)
        )

    def _fulltext(self, doc: DocumentRecord, result: IngestResult, ctx: StageContext) -> None:
        pending = [c for c in doc.chunks if c.enabled and not c.fulltext_indexed]
        if self.fulltext is None or not pending:
            return
        try:
            self._write_fulltext(self.fulltext, doc, pending=pending, ctx=ctx)
        except LeaseLost:
            raise
        except Exception as exc:
            record_degradation("fulltext_index", REASON_INDEX_WRITE_FAILED, exc, doc_id=doc.doc_id)
            result.error = result.error or f"fulltext_failed: {type(exc).__name__}"

    def _write_fulltext(
        self,
        index: FulltextStore,
        doc: DocumentRecord,
        *,
        pending: list[ChunkRecord],
        ctx: StageContext,
    ) -> None:
        staged = [stamp_stage(c, STAGE_FULLTEXT, generation=ctx.generation) for c in pending]
        ctx.guard()
        if index.index(staged) != len(staged):
            raise RuntimeError("Incomplete fulltext write")
        for chunk, written in zip(pending, staged, strict=True):
            chunk.fulltext_indexed = True
            chunk.stages = written.stages
        ctx.guard()
        self.docs.save_chunks(doc, pending)

    def _graph(
        self, doc: DocumentRecord, result: IngestResult, *, batch_id: str, ctx: StageContext
    ) -> None:
        if self.graph is None:
            return
        run_graph_stage(doc, result, graph=self.graph, docs=self.docs, batch_id=batch_id, ctx=ctx)
