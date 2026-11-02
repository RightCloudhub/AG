"""Persistent per-chunk stages; successful writes precede progress checkpoints."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace

from agentic_graphrag.core.ports.stores import (
    ChunkRecord,
    DocStore,
    DocumentRecord,
    FulltextStore,
    VectorStore,
)
from agentic_graphrag.knowledge.document_lifecycle import DocumentLifecycle
from agentic_graphrag.knowledge.embedding_batch import BatchEmbedder, embed_with_retry, status_code
from agentic_graphrag.knowledge.graph_ingest import GraphIngestor
from agentic_graphrag.knowledge.index_config import IngestWorkerConfig
from agentic_graphrag.knowledge.index_result import IngestResult
from agentic_graphrag.knowledge.ingest import chunk_document


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
            if not doc.metadata.get("chunks_initialized"):
                doc.chunks = chunk_document(
                    doc,
                    chunk_size=self.config.chunk_size_chars,
                    overlap=self.config.chunk_overlap_chars,
                )
                doc.metadata["chunks_initialized"] = True
                self.docs.save(doc)
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
                self._vector_batch(doc, batch, heartbeat)
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
            ready = [c for c in doc.chunks if c.embedded and c not in pending]
            self._restore_vectors(ready)
        return pending

    def _restore_vectors(self, chunks: list[ChunkRecord]) -> None:
        size = self.config.embedding_batch_size
        for offset in range(0, len(chunks), size):
            batch = chunks[offset : offset + size]
            if self.vector.upsert(batch) != len(batch):
                raise RuntimeError("Incomplete staged vector write")

    def _vector_batch(
        self, doc: DocumentRecord, batch: list[ChunkRecord], heartbeat: Callable[[], None]
    ) -> None:
        assert self.embed_many is not None
        vectors = embed_with_retry(
            self.embed_many, [c.text for c in batch], config=self.config, heartbeat=heartbeat
        )
        if len(vectors) != len(batch) or not all(_valid_vector(v) for v in vectors):
            raise ValueError("Embedding response count or vector is invalid")
        staged = [
            replace(c, embedding=v, embedded=True, embedding_model=self.config.embedding_model)
            for c, v in zip(batch, vectors, strict=True)
        ]
        # Upsert replaces a logical chunk atomically: old vectors remain on failure.
        if self.vector.upsert(staged) != len(staged):
            raise RuntimeError("Incomplete vector write")
        for chunk, vector in zip(batch, vectors, strict=True):
            chunk.embedding = vector
            chunk.embedded = True
            chunk.embedding_model = self.config.embedding_model
        self.docs.save(doc)

    def _fulltext(self, doc: DocumentRecord, result: IngestResult) -> None:
        pending = [c for c in doc.chunks if not c.fulltext_indexed]
        if self.fulltext is None or not pending:
            return
        try:
            if self.fulltext.index(pending) != len(pending):
                raise RuntimeError("Incomplete fulltext write")
            for chunk in pending:
                chunk.fulltext_indexed = True
            self.docs.save(doc)
        except Exception as exc:
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
        result.graph = {"accepted": 0, "conflicts_review": 0}
        for chunk in doc.chunks:
            heartbeat()
            if not chunk.graph_extracted:
                self._graph_chunk(doc, chunk, batch_id=batch_id)
            outcome = chunk.metadata.get("graph_result", {})
            for key in ("accepted", "conflicts_review"):
                result.graph[key] += int(outcome.get(key, 0))
            result.error = result.error or outcome.get("error")

    def _graph_chunk(self, doc: DocumentRecord, chunk: ChunkRecord, *, batch_id: str) -> None:
        assert self.graph is not None
        try:
            outcome = self.graph.ingest_chunks([chunk], tenant_id=doc.tenant_id, batch_id=batch_id)
            chunk.metadata["graph_result"] = outcome.to_dict()
            chunk.graph_extracted = not outcome.error
        except Exception as exc:
            chunk.metadata["graph_result"] = {"error": f"graph_failed: {type(exc).__name__}"}
        self.docs.save(doc)


def _valid_vector(vector: list[float]) -> bool:
    return bool(vector) and all(math.isfinite(value) for value in vector)
