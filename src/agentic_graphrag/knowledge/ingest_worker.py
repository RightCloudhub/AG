"""Background ingest worker: chunk → embed → index uploaded docs (ENT-06).

Usage (standalone)::

    python -m agentic_graphrag.knowledge.ingest_worker --once

Or use ``IngestWorker`` as a background thread in the API process.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from collections.abc import Callable

from agentic_graphrag.core.ports.capabilities import VectorModelRebuild
from agentic_graphrag.core.ports.stores import DocStore, FulltextStore, VectorStore
from agentic_graphrag.knowledge.embedding_batch import BatchEmbedder, single_adapter
from agentic_graphrag.knowledge.graph_ingest import GraphIngestor
from agentic_graphrag.knowledge.index_config import IngestWorkerConfig as IngestWorkerConfig
from agentic_graphrag.knowledge.index_pipeline import IndexPipeline
from agentic_graphrag.knowledge.index_result import (
    IngestResult as IngestResult,
)
from agentic_graphrag.knowledge.index_result import (
    task_outcome as task_outcome,
)
from agentic_graphrag.knowledge.index_result import (
    total_counts,
)
from agentic_graphrag.knowledge.ingest_tasks import (
    IngestStatus,
    IngestTask,
    IngestTaskStore,
)

logger = logging.getLogger(__name__)

INDEXED_MESSAGE = "Indexed successfully"
REVIEW_MESSAGE_PREFIX = "Indexed; "


def _new_worker_id() -> str:
    """Lease token identifying this worker instance (see ``IngestTaskStore``)."""
    return f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"


class IngestWorker:
    """Process unindexed documents from the doc store into vector/fulltext indexes.

    Chunk progress is persisted in DocStore after every successful stage.
    """

    def __init__(
        self,
        *,
        doc_store: DocStore,
        vector_store: VectorStore,
        fulltext_store: FulltextStore | None = None,
        embed_fn: Callable[[str], list[float]] | None = None,
        embed_many: BatchEmbedder | None = None,
        on_commit: Callable[[], None] | None = None,
        task_store: IngestTaskStore | None = None,
        config: IngestWorkerConfig | None = None,
        worker_id: str | None = None,
        graph_ingestor: GraphIngestor | None = None,
    ) -> None:
        self.graph_ingestor = graph_ingestor
        self.doc_store = doc_store
        self.vector_store = vector_store
        self.fulltext_store = fulltext_store
        self.embed_fn = embed_fn
        self.config = config or IngestWorkerConfig()
        self.task_store = task_store
        self.worker_id = worker_id or _new_worker_id()
        batch_embedder = embed_many
        if batch_embedder is None and embed_fn is not None:
            batch_embedder = single_adapter(embed_fn)
        self.pipeline = IndexPipeline(
            doc_store,
            vector=vector_store,
            fulltext=fulltext_store,
            graph=graph_ingestor,
            embed_many=batch_embedder,
            config=self.config,
            on_commit=on_commit,
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def process_doc(
        self, doc_id: str, *, batch_id: str = "", tenant_id: str | None = None
    ) -> IngestResult:
        def heartbeat() -> None:
            if batch_id and self.task_store:
                if not self.task_store.touch(batch_id, owner=self.worker_id):
                    raise RuntimeError("Task lease lost")

        return self.pipeline.process(
            doc_id, tenant_id=tenant_id, batch_id=batch_id, heartbeat=heartbeat
        )

    def run_once(self) -> list[IngestResult]:
        if self.task_store is not None:
            return self._run_task_batch()
        results = []
        for doc_id, tenant in self.doc_store.list_refs():
            doc = self.doc_store.get(doc_id, tenant_id=tenant)
            if doc and (doc.metadata.get("lifecycle") or not doc.metadata.get("index_complete")):
                results.append(self.process_doc(doc_id, tenant_id=tenant))
            if len(results) >= self.config.batch_size:
                break
        return results

    def reconcile(self, *, tenant_id: str) -> list[IngestResult]:
        results = self._reconcile_tasks(tenant_id)
        attempted = {result.doc_id for result in results}
        for did in self.doc_store.list_ids(tenant_id=tenant_id):
            if did in attempted:
                continue
            doc = self.doc_store.get(did, tenant_id=tenant_id)
            if doc and (doc.metadata.get("lifecycle") or not doc.metadata.get("index_complete")):
                results.append(self.process_doc(did, tenant_id=tenant_id))
        return results

    def _reconcile_tasks(self, tenant_id: str) -> list[IngestResult]:
        results: list[IngestResult] = []
        if self.task_store is None:
            return results
        _, total = self.task_store.list(tenant_id=tenant_id, limit=1)
        tasks, _ = self.task_store.list(tenant_id=tenant_id, limit=max(total, 1))
        for task in tasks:
            if task.status not in {IngestStatus.PARTIAL, IngestStatus.FAILED}:
                continue
            self.task_store.transition(task.id, IngestStatus.QUEUED)
            if self._claim(task.id):
                results.extend(self._run_task(task))
        return results

    def reindex(self, *, tenant_id: str) -> list[IngestResult]:
        if self.pipeline.embed_many is None:
            raise ValueError("Reindex requires an embedder")
        with self.doc_store.lock():
            rebuild = (
                self.vector_store if isinstance(self.vector_store, VectorModelRebuild) else None
            )
            if rebuild:
                rebuild.begin_reindex(self.config.embedding_model, tenant_id=tenant_id)
            try:
                results = [
                    self.pipeline.process(did, tenant_id=tenant_id, reindex=True)
                    for did in self.doc_store.list_ids(tenant_id=tenant_id)
                ]
                if rebuild and not any(r.error for r in results):
                    rebuild.commit_reindex()
                return results
            finally:
                if rebuild:
                    rebuild.end_reindex()

    def _run_task_batch(self) -> list[IngestResult]:
        requeued = self._queue.requeue_stale(
            stale_seconds=self.config.stale_task_seconds,
            exclude_owner=self.worker_id,
        )
        if requeued:
            logger.warning("Requeued %d stale extracting task(s): %s", len(requeued), requeued)
        results: list[IngestResult] = []
        for task in self._queue.pending(self.config.batch_size):
            if self._claim(task.id):
                results.extend(self._run_task(task))
        return results

    @property
    def _queue(self) -> IngestTaskStore:
        """The task store, present on every path that reaches this property."""
        assert self.task_store is not None
        return self.task_store

    def _claim(self, task_id: str) -> bool:
        """Take the lease on a queued task; False when someone else has it."""
        try:
            self._queue.transition(task_id, IngestStatus.EXTRACTING, owner=self.worker_id)
        except (KeyError, ValueError) as exc:
            logger.info("Skipping task %s: %s", task_id, exc)
            return False
        return True

    def _run_task(self, task: IngestTask) -> list[IngestResult]:
        """Index one claimed task's documents, then record its terminal status."""
        try:
            results = self._process_task_docs(task)
        except Exception as exc:  # noqa: BLE001
            self._finalize(task.id, IngestStatus.FAILED, type(exc).__name__)
            return [IngestResult("", error=type(exc).__name__, fatal=True)]
        self._finalize(task.id, *task_outcome(results), counts=total_counts(results))
        return results

    def _process_task_docs(self, task: IngestTask) -> list[IngestResult]:
        """Index every document, refreshing the lease between documents."""
        results: list[IngestResult] = []
        for row in task.docs:
            if row.get("status") == "duplicate":
                continue
            if row.get("error"):
                results.append(IngestResult(row.get("doc_id", ""), error=row["error"], fatal=True))
                continue
            current = self.doc_store.get(row["doc_id"], tenant_id=task.tenant_id)
            if current and row.get("version") and str(current.version) != row["version"]:
                continue
            results.append(
                self.process_doc(row["doc_id"], batch_id=task.id, tenant_id=task.tenant_id)
            )
            self._queue.touch(task.id, owner=self.worker_id)
        return results

    def _finalize(
        self,
        task_id: str,
        status: IngestStatus,
        message: str,
        *,
        counts: dict[str, int] | None = None,
    ) -> None:
        """Record the terminal status — bookkeeping must never look like failure.

        A stale requeue racing this worker puts the task back in QUEUED, which
        makes EXTRACTING → DONE invalid. That ValueError used to be caught by the
        batch handler and rewritten as FAILED, marking a *successful* task failed
        (docs/design/BUSINESS_LOGIC.md BL-06). Log it and leave the status alone.
        """
        try:
            self._queue.transition(
                task_id, status, message=message, owner=self.worker_id, counts=counts
            )
        except (KeyError, ValueError) as exc:
            logger.error("Cannot record %s for task %s: %s", status.value, task_id, exc)

    def start(self) -> None:
        """Start background polling thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ingest-worker")
        self._thread.start()

    def stop(self) -> None:
        """Signal the background thread to stop."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        logger.info("IngestWorker started")
        while not self._stop.is_set():
            try:
                results = self.run_once()
                if results:
                    logger.info(
                        "Ingested %d docs: %s",
                        len(results),
                        ", ".join(r.doc_id for r in results),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error("IngestWorker error: %s", exc)
            self._stop.wait(timeout=self.config.poll_interval_seconds)
        logger.info("IngestWorker stopped")


if __name__ == "__main__":
    from agentic_graphrag.knowledge.ingest_worker_cli import main

    main()
