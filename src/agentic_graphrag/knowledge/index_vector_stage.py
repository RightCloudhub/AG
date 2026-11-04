"""Vector stage of :class:`IndexPipeline` (300-line gate split).

Successful vector writes precede checkpoints. Each written chunk records its
stage state, revision, model and generation, so physical vector identities are
revision- and generation-scoped (K-05). Disabled chunks are never embedded.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

from agentic_graphrag.core.enums import RetrievalChannel
from agentic_graphrag.core.ports.stores import (
    STAGE_VECTOR,
    ChunkRecord,
    DocStore,
    DocumentRecord,
    VectorStore,
)
from agentic_graphrag.knowledge.embedding_batch import BatchEmbedder, embed_with_retry, status_code
from agentic_graphrag.knowledge.index_config import IngestWorkerConfig
from agentic_graphrag.knowledge.index_result import IngestResult
from agentic_graphrag.knowledge.ingest_tasks import LeaseLost
from agentic_graphrag.observability.degradation import (
    REASON_DOC_WRITE_FAILED,
    REASON_EMBEDDING_UNAVAILABLE,
    REASON_INDEX_WRITE_FAILED,
    degradation_guard,
    record_degradation,
)


def stamp_stage(chunk: ChunkRecord, stage: str, *, generation: int, model: str = "") -> ChunkRecord:
    """Copy of ``chunk`` whose ``stage`` is ready at its current revision."""
    stages = dict(chunk.stages)
    stages[stage] = {
        "state": "ready",
        "revision": chunk.revision,
        "generation": generation,
        "error": None,
        "model": model or None,
    }
    return replace(chunk, stages=stages)


@dataclass
class StageContext:
    """Per-run inputs shared by stage functions."""

    heartbeat: Callable[[], None]
    generation: int = 0
    reindex: bool = False
    guard: Callable[[], None] = lambda: None


class VectorStage:
    def __init__(
        self,
        docs: DocStore,
        vector: VectorStore,
        embed_many: BatchEmbedder,
        config: IngestWorkerConfig,
    ) -> None:
        self.docs, self.vector, self.embed_many, self.config = docs, vector, embed_many, config

    def run(self, doc: DocumentRecord, result: IngestResult, ctx: StageContext) -> None:
        pending = self._pending(doc, ctx)
        size = self.config.embedding_batch_size
        for offset in range(0, len(pending), size):
            batch = pending[offset : offset + size]
            try:
                self._batch(doc, batch, ctx)
            except LeaseLost:
                raise
            except Exception as exc:
                code = status_code(exc)
                result.error = f"embedding_failed: {code or type(exc).__name__}"
                result.fatal = code == 401
                if result.fatal:
                    return

    def _pending(self, doc: DocumentRecord, ctx: StageContext) -> list[ChunkRecord]:
        model = self.config.embedding_model
        pending = [
            c
            for c in doc.chunks
            if c.enabled and (not c.embedded or (ctx.reindex and c.embedding_model != model))
        ]
        if ctx.reindex:
            pending_ids = {c.chunk_id for c in pending}
            ready = [c for c in doc.chunks if c.embedded and c.chunk_id not in pending_ids]
            self._restore(ready)
        return pending

    def _restore(self, chunks: list[ChunkRecord]) -> None:
        size = self.config.embedding_batch_size
        for offset in range(0, len(chunks), size):
            batch = chunks[offset : offset + size]
            if self.vector.upsert(batch) != len(batch):
                raise RuntimeError("Incomplete staged vector write")

    def _embed_missing(self, batch: list[ChunkRecord], ctx: StageContext) -> dict[str, list[float]]:
        missing = [c for c in batch if self._needs_embedding(c, reindex=ctx.reindex)]
        if not missing:
            return {}
        texts = [c.text for c in missing]
        try:
            vectors = checked_vectors(
                embed_with_retry(
                    self.embed_many, texts, config=self.config, heartbeat=ctx.heartbeat
                ),
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
        return not valid_vector(chunk.embedding or []) or (
            reindex and chunk.embedding_model != self.config.embedding_model
        )

    def _batch(self, doc: DocumentRecord, batch: list[ChunkRecord], ctx: StageContext) -> None:
        new = self._embed_missing(batch, ctx)
        staged = [self._staged(c, new, ctx) for c in batch]
        ctx.guard()
        # Upsert replaces a logical chunk atomically: old vectors remain on failure.
        with degradation_guard("vector_index", REASON_INDEX_WRITE_FAILED, doc_id=doc.doc_id):
            if self.vector.upsert(staged) != len(staged):
                raise RuntimeError("Incomplete vector write")
        for chunk, written in zip(batch, staged, strict=True):
            chunk.embedding = written.embedding
            chunk.embedded = True
            chunk.embedding_model = written.embedding_model
            chunk.stages = written.stages
        ctx.guard()
        with degradation_guard("doc_store", REASON_DOC_WRITE_FAILED, doc_id=doc.doc_id):
            self.docs.save_chunks(doc, batch)

    def _staged(
        self, chunk: ChunkRecord, new: dict[str, list[float]], ctx: StageContext
    ) -> ChunkRecord:
        model = self.config.embedding_model if chunk.chunk_id in new else chunk.embedding_model
        stamped = stamp_stage(chunk, STAGE_VECTOR, generation=ctx.generation, model=model)
        return replace(
            stamped,
            embedding=new.get(chunk.chunk_id, chunk.embedding),
            embedded=True,
            embedding_model=model,
        )


def checked_vectors(vectors: list[list[float]], *, count: int) -> list[list[float]]:
    if len(vectors) != count or not all(valid_vector(v) for v in vectors):
        raise ValueError("Embedding response count or vector is invalid")
    return vectors


def valid_vector(vector: list[float]) -> bool:
    return bool(vector) and all(math.isfinite(value) for value in vector)
