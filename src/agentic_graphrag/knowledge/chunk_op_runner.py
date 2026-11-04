"""Worker side of chunk edit/disable/re-enable operations (K-05, release 5a).

All stages of one document run under the document lease; every stage commit is a
fenced, revision-checked doc-store transaction, and every cleanup is bounded by a
revision-scoped identity, so a worker that lost its lease during a blocked
provider call can neither publish nor delete newer content.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from agentic_graphrag.core.ports.document_ops import DocOperation, Fence, OpState
from agentic_graphrag.core.ports.document_ops_store import DocumentOpsStore
from agentic_graphrag.core.ports.stores import (
    INDEX_STAGES,
    STAGE_FULLTEXT,
    STAGE_GRAPH,
    STAGE_VECTOR,
    ChunkIdentity,
    ChunkKey,
    ChunkRecord,
    ChunkScopedGraph,
    ChunkScopedIndex,
    DocumentRecord,
)
from agentic_graphrag.knowledge.embedding_batch import embed_with_retry
from agentic_graphrag.knowledge.index_pipeline import IndexPipeline
from agentic_graphrag.knowledge.index_result import IngestResult
from agentic_graphrag.knowledge.index_vector_stage import (
    StageContext,
    checked_vectors,
    stamp_stage,
    valid_vector,
)
from agentic_graphrag.knowledge.ingest_tasks import LeaseLost
from agentic_graphrag.observability.degradation import REASON_INDEX_WRITE_FAILED, record_degradation

DOCUMENT_CHANGED = "document_changed"


class StageRejected(RuntimeError):
    """The fenced commit failed: lease lost or the chunk moved to a newer revision."""


class ChunkOpRunner:
    def __init__(self, pipeline: IndexPipeline, docs: DocumentOpsStore) -> None:
        self.pipeline, self.docs = pipeline, docs

    def run(self, op: DocOperation, *, heartbeat: Callable[[], None]) -> IngestResult:
        fence = self.pipeline.acquire(op.tenant_id, op.doc_id)
        assert fence is not None  # DocumentOpsStore always supports leases
        try:
            ctx = self.pipeline.context(fence, heartbeat=heartbeat, reindex=False)
            return self._run(op, fence, ctx)
        finally:
            self.pipeline.release(fence)

    def _run(self, op: DocOperation, fence: Fence, ctx: StageContext) -> IngestResult:
        self.docs.update_op(op.op_id, state=OpState.RUNNING.value)
        doc = self.docs.get(op.doc_id, tenant_id=op.tenant_id)
        chunk = _chunk(doc, str(op.payload.get("chunk_id", "")))
        if doc is None or chunk is None or doc.version != op.payload.get("doc_version"):
            self.docs.update_op(op.op_id, state=OpState.CONFLICT.value, error=DOCUMENT_CHANGED)
            return IngestResult(op.doc_id, error=DOCUMENT_CHANGED, fatal=True)
        if chunk.revision != int(op.payload.get("revision", 0)):
            self.docs.update_op(op.op_id, state=OpState.DONE.value, error="superseded")
            return IngestResult(op.doc_id)
        try:
            error = self._stages(doc, chunk, fence=fence, ctx=ctx)
        except StageRejected:
            self.docs.update_op(op.op_id, state=OpState.DONE.value, error="superseded")
            return IngestResult(op.doc_id)
        state = OpState.FAILED if error else OpState.DONE
        self.docs.update_op(op.op_id, state=state.value, error=error or "")
        return IngestResult(op.doc_id, chunks_indexed=int(not error), error=error)

    def _stages(
        self, doc: DocumentRecord, chunk: ChunkRecord, *, fence: Fence, ctx: StageContext
    ) -> str | None:
        key = ChunkKey(doc.tenant_id or "default", doc.doc_id, chunk.chunk_id)
        if not chunk.enabled:
            self._disable(doc, chunk, scope=(key, fence), ctx=ctx)
            return None
        errors = []
        for stage, runner in (
            (STAGE_VECTOR, self._vector),
            (STAGE_FULLTEXT, self._fulltext),
            (STAGE_GRAPH, self._graph),
        ):
            try:
                chunk = runner(doc, chunk, scope=(key, fence), ctx=ctx)
            except (LeaseLost, StageRejected):
                raise
            except Exception as exc:  # noqa: BLE001 — recorded on the chunk and the task
                record_degradation(f"{stage}_index", REASON_INDEX_WRITE_FAILED, exc)
                errors.append(f"{stage}_failed: {type(exc).__name__}")
                chunk = self._fail(doc, chunk, (stage, errors[-1]), fence=fence)
        return "; ".join(errors) or None

    def _commit(self, doc: DocumentRecord, chunk: ChunkRecord, fence: Fence) -> ChunkRecord:
        if not self.docs.commit_chunk_stage(chunk, fence=fence, doc=doc):
            raise StageRejected(chunk.chunk_id)
        return chunk

    def _fail(
        self, doc: DocumentRecord, chunk: ChunkRecord, failure: tuple[str, str], *, fence: Fence
    ) -> ChunkRecord:
        stage, error = failure
        stages = {**chunk.stages, stage: {**chunk.stages.get(stage, {}), "error": error}}
        stages[stage]["state"] = "failed"
        return self._commit(doc, replace(chunk, stages=stages), fence)

    def _disable(
        self,
        doc: DocumentRecord,
        chunk: ChunkRecord,
        *,
        scope: tuple[ChunkKey, Fence],
        ctx: StageContext,
    ) -> None:
        key, fence = scope
        # Everything up to and including this revision is obsolete; a later re-enable
        # writes a higher revision that this cleanup can never reach.
        below = ChunkIdentity(doc.version, chunk.revision + 1, 0)
        for index in (self.pipeline.vector, self.pipeline.fulltext):
            if isinstance(index, ChunkScopedIndex):
                ctx.guard()
                index.delete_chunk(key, below=below)
        graph = self.pipeline.graph.graph if self.pipeline.graph else None
        if isinstance(graph, ChunkScopedGraph):
            ctx.guard()
            graph.delete_chunk_support(key, below=below)
        stages = {
            stage: {**chunk.stages.get(stage, {}), "state": "disabled", "error": None}
            for stage in INDEX_STAGES
        }
        self._commit(doc, replace(chunk, stages=stages), fence)

    def _vector(
        self,
        doc: DocumentRecord,
        chunk: ChunkRecord,
        *,
        scope: tuple[ChunkKey, Fence],
        ctx: StageContext,
    ) -> ChunkRecord:
        embed = self.pipeline.embed_many
        if embed is None:
            return chunk
        model = self.pipeline.config.embedding_model
        chunk = stamp_stage(chunk, STAGE_VECTOR, generation=scope[1].token, model=model)
        vector = chunk.embedding or []
        if not valid_vector(vector) or chunk.embedding_model != model:
            fresh = embed_with_retry(
                embed, [chunk.text], config=self.pipeline.config, heartbeat=ctx.heartbeat
            )
            vector = checked_vectors(fresh, count=1)[0]
        ready = replace(chunk, embedding=vector, embedding_model=model, embedded=True)
        ctx.guard()
        if self.pipeline.vector.upsert([ready]) != 1:
            raise RuntimeError("Incomplete vector write")
        committed = self._commit(doc, ready, scope[1])
        self._cleanup(self.pipeline.vector, scope, below=ready.identity(STAGE_VECTOR), ctx=ctx)
        return committed

    def _fulltext(
        self,
        doc: DocumentRecord,
        chunk: ChunkRecord,
        *,
        scope: tuple[ChunkKey, Fence],
        ctx: StageContext,
    ) -> ChunkRecord:
        index = self.pipeline.fulltext
        if index is None:
            return chunk
        stamped = stamp_stage(chunk, STAGE_FULLTEXT, generation=scope[1].token)
        ready = replace(stamped, fulltext_indexed=True)
        ctx.guard()
        if index.index([ready]) != 1:
            raise RuntimeError("Incomplete fulltext write")
        committed = self._commit(doc, ready, scope[1])
        self._cleanup(index, scope, below=ready.identity(STAGE_FULLTEXT), ctx=ctx)
        return committed

    def _graph(
        self,
        doc: DocumentRecord,
        chunk: ChunkRecord,
        *,
        scope: tuple[ChunkKey, Fence],
        ctx: StageContext,
    ) -> ChunkRecord:
        ingestor = self.pipeline.graph
        if ingestor is None:
            return chunk
        chunk = stamp_stage(chunk, STAGE_GRAPH, generation=scope[1].token)
        if isinstance(ingestor.graph, ChunkScopedGraph):
            # Remove the old revision's support first, so the edited text is not
            # judged against facts extracted from the text it replaces.
            ctx.guard()
            ingestor.graph.delete_chunk_support(scope[0], below=chunk.identity(STAGE_GRAPH))
        ctx.guard()
        chunk.metadata["document_version"] = doc.version
        outcome = ingestor.ingest_chunks([chunk], tenant_id=doc.tenant_id, batch_id="")
        if outcome.error:
            raise RuntimeError(outcome.error)
        metadata = {**chunk.metadata, "graph_result": outcome.to_dict()}
        return self._commit(doc, replace(chunk, graph_extracted=True, metadata=metadata), scope[1])

    @staticmethod
    def _cleanup(
        index: object,
        scope: tuple[ChunkKey, Fence],
        *,
        below: ChunkIdentity,
        ctx: StageContext,
    ) -> None:
        if isinstance(index, ChunkScopedIndex):
            ctx.guard()
            index.delete_chunk(scope[0], below=below)


def _chunk(doc: DocumentRecord | None, chunk_id: str) -> ChunkRecord | None:
    if doc is None:
        return None
    return next((c for c in doc.chunks if c.chunk_id == chunk_id), None)
