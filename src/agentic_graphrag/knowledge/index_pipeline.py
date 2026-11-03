"""Persistent per-chunk stages; successful writes precede progress checkpoints."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace

from agentic_graphrag.core.enums import RetrievalChannel
from agentic_graphrag.core.ports.stores import (
    ChunkRecord,
    DocStore,
    DocumentRecord,
    FulltextStore,
    VectorStore,
)
from agentic_graphrag.knowledge.chunk_splitting import ChunkingError
from agentic_graphrag.knowledge.document_lifecycle import DocumentLifecycle
from agentic_graphrag.knowledge.embedding_batch import BatchEmbedder, embed_with_retry, status_code
from agentic_graphrag.knowledge.graph_ingest import GraphIngestor
from agentic_graphrag.knowledge.index_config import IngestWorkerConfig
from agentic_graphrag.knowledge.index_graph_stage import run_graph_stage
from agentic_graphrag.knowledge.index_recovery import invalidate_stages, stale_stages
from agentic_graphrag.knowledge.index_result import IngestResult
from agentic_graphrag.knowledge.ingest import chunk_document
from agentic_graphrag.knowledge.ingest_tasks import LeaseLost
from agentic_graphrag.observability.degradation import (
    REASON_DOC_WRITE_FAILED,
    REASON_EMBEDDING_UNAVAILABLE,
    REASON_INDEX_WRITE_FAILED,
    degradation_guard,
    record_degradation,
)


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
        with self.docs.lock():
            doc = self.docs.get(doc_id, tenant_id=tenant_id)
            if doc is None:
                return IngestResult(doc_id, error="not_found", fatal=True)
            doc = self.lifecycle.resume(doc)
            if doc is None:
                return IngestResult(doc_id)
            generations = stale_stages(doc, self._indexes(reindex=reindex))
            if generations:
                invalidate_stages(doc, generations)
                self.docs.save(doc)
            chunk_error = self._ensure_chunks(doc)
            if chunk_error:
                return IngestResult(doc_id, error=chunk_error, fatal=True, unchunkable=True)
            result = IngestResult(doc_id, chunks_created=len(doc.chunks))
            self._vectors(doc, result, heartbeat=heartbeat, reindex=reindex)
            if not reindex:
                self._fulltext(doc, result)
                self._graph(doc, result, batch_id=batch_id, heartbeat=heartbeat)
            result.counts = self.counts(doc)
            result.chunks_indexed = result.counts["embedded"]
            doc.metadata["index_complete"] = result.counts["incomplete"] == 0
            doc.metadata["index_error"] = result.error
            self.docs.save(doc)
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
        return (
            (self.embed_many is None or chunk.embedded)
            and (self.fulltext is None or chunk.fulltext_indexed)
            and (self.graph is None or chunk.graph_extracted)
        )

    def _vectors(
        self,
        doc: DocumentRecord,
        result: IngestResult,
        *,
        heartbeat: Callable[[], None],
        reindex: bool,
    ) -> None:
        if self.embed_many is None:
            return
        pending = self._pending_vectors(doc, reindex=reindex)
        size = self.config.embedding_batch_size
        for offset in range(0, len(pending), size):
            batch = pending[offset : offset + size]
            try:
                self._vector_batch(doc, batch, heartbeat=heartbeat, reindex=reindex)
            except LeaseLost:
                raise
            except Exception as exc:
                code = status_code(exc)
                result.error = f"embedding_failed: {code or type(exc).__name__}"
                result.fatal = code == 401
                if result.fatal:
                    return

    def _pending_vectors(self, doc: DocumentRecord, *, reindex: bool) -> list[ChunkRecord]:
        pending = [
            c
            for c in doc.chunks
            if not c.embedded or (reindex and c.embedding_model != self.config.embedding_model)
        ]
        if reindex:
            pending_ids = {c.chunk_id for c in pending}
            ready = [c for c in doc.chunks if c.embedded and c.chunk_id not in pending_ids]
            self._restore_vectors(ready)
        return pending

    def _restore_vectors(self, chunks: list[ChunkRecord]) -> None:
        size = self.config.embedding_batch_size
        for offset in range(0, len(chunks), size):
            batch = chunks[offset : offset + size]
            if self.vector.upsert(batch) != len(batch):
                raise RuntimeError("Incomplete staged vector write")

    def _embed_missing(
        self,
        batch: list[ChunkRecord],
        *,
        heartbeat: Callable[[], None],
        reindex: bool,
    ) -> dict[str, list[float]]:
        assert self.embed_many is not None
        missing = [c for c in batch if self._needs_embedding(c, reindex=reindex)]
        if not missing:
            return {}
        texts = [c.text for c in missing]
        try:
            vectors = _checked_vectors(
                embed_with_retry(self.embed_many, texts, config=self.config, heartbeat=heartbeat),
                count=len(missing),
            )
        except LeaseLost:
            raise
        except Exception as exc:
            record_degradation(
                RetrievalChannel.EMBEDDING,
                REASON_EMBEDDING_UNAVAILABLE,
                exc,
                doc_id=missing[0].doc_id,
                chunks=len(missing),
            )
            raise
        return {c.chunk_id: v for c, v in zip(missing, vectors, strict=True)}

    def _needs_embedding(self, chunk: ChunkRecord, *, reindex: bool) -> bool:
        return not _valid_vector(chunk.embedding or []) or (
            reindex and chunk.embedding_model != self.config.embedding_model
        )

    def _vector_batch(
        self,
        doc: DocumentRecord,
        batch: list[ChunkRecord],
        *,
        heartbeat: Callable[[], None],
        reindex: bool,
    ) -> None:
        new = self._embed_missing(batch, heartbeat=heartbeat, reindex=reindex)
        staged = [
            replace(
                c,
                embedding=new.get(c.chunk_id, c.embedding),
                embedded=True,
                embedding_model=self.config.embedding_model
                if c.chunk_id in new
                else c.embedding_model,
            )
            for c in batch
        ]
        # Upsert replaces a logical chunk atomically: old vectors remain on failure.
        with degradation_guard("vector_index", REASON_INDEX_WRITE_FAILED, doc_id=doc.doc_id):
            if self.vector.upsert(staged) != len(staged):
                raise RuntimeError("Incomplete vector write")
        for chunk, written in zip(batch, staged, strict=True):
            chunk.embedding = written.embedding
            chunk.embedded = True
            chunk.embedding_model = written.embedding_model
        with degradation_guard("doc_store", REASON_DOC_WRITE_FAILED, doc_id=doc.doc_id):
            self.docs.save_chunks(doc, batch)

    def _fulltext(self, doc: DocumentRecord, result: IngestResult) -> None:
        pending = [c for c in doc.chunks if not c.fulltext_indexed]
        if self.fulltext is None or not pending:
            return
        try:
            if self.fulltext.index(pending) != len(pending):
                raise RuntimeError("Incomplete fulltext write")
            for chunk in pending:
                chunk.fulltext_indexed = True
            self.docs.save_chunks(doc, pending)
        except Exception as exc:
            record_degradation("fulltext_index", REASON_INDEX_WRITE_FAILED, exc, doc_id=doc.doc_id)
            result.error = result.error or f"fulltext_failed: {type(exc).__name__}"

    def _graph(
        self,
        doc: DocumentRecord,
        result: IngestResult,
        *,
        batch_id: str,
        heartbeat: Callable[[], None],
    ) -> None:
        if self.graph is None:
            return
        run_graph_stage(
            doc, result, graph=self.graph, docs=self.docs, batch_id=batch_id, heartbeat=heartbeat
        )


def _checked_vectors(vectors: list[list[float]], *, count: int) -> list[list[float]]:
    if len(vectors) != count or not all(_valid_vector(v) for v in vectors):
        raise ValueError("Embedding response count or vector is invalid")
    return vectors


def _valid_vector(vector: list[float]) -> bool:
    return bool(vector) and all(math.isfinite(value) for value in vector)
