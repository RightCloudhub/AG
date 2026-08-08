"""Background ingest worker: chunk → embed → index uploaded docs (ENT-06).

Usage (standalone)::

    python -m agentic_graphrag.knowledge.ingest_worker --once

Or use ``IngestWorker`` as a background thread in the API process.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from agentic_graphrag.knowledge.ingest import chunk_document
from agentic_graphrag.knowledge.ingest_tasks import (
    DEFAULT_STALE_SECONDS,
    IngestStatus,
    IngestTaskStore,
)
from agentic_graphrag.stores.interfaces import DocStore, VectorStore

logger = logging.getLogger(__name__)

INDEXED_MESSAGE = "Indexed successfully"


def _new_worker_id() -> str:
    """Lease token identifying this worker instance (see ``IngestTaskStore``)."""
    return f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"


@dataclass
class IngestResult:
    doc_id: str
    chunks_created: int = 0
    chunks_indexed: int = 0
    error: str | None = None


@dataclass
class IngestWorkerConfig:
    """Tuning knobs for the background ingest loop."""

    poll_interval_seconds: float = 5.0
    batch_size: int = 10
    chunk_size_chars: int = 1200
    chunk_overlap_chars: int = 150
    stale_task_seconds: float = DEFAULT_STALE_SECONDS


class IngestWorker:
    """Process unindexed documents from the doc store into vector/fulltext indexes.

    Tracks which doc_ids have been processed via an in-memory set (sufficient
    for single-process deployments; upgrade to persistent journal for multi-node).
    """

    def __init__(
        self,
        *,
        doc_store: DocStore,
        vector_store: VectorStore,
        fulltext_store: Any | None = None,
        embed_fn: Any | None = None,
        task_store: IngestTaskStore | None = None,
        config: IngestWorkerConfig | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.doc_store = doc_store
        self.vector_store = vector_store
        self.fulltext_store = fulltext_store
        self.embed_fn = embed_fn
        self.config = config or IngestWorkerConfig()
        self.task_store = task_store
        self.worker_id = worker_id or _new_worker_id()
        self._processed: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def process_doc(self, doc_id: str) -> IngestResult:
        """Process a single document: chunk → embed → upsert."""
        doc = self.doc_store.get(doc_id)
        if doc is None:
            return IngestResult(doc_id=doc_id, error="not_found")
        chunks = chunk_document(
            doc,
            chunk_size=self.config.chunk_size_chars,
            overlap=self.config.chunk_overlap_chars,
        )
        if not chunks:
            self._processed.add(doc_id)
            return IngestResult(doc_id=doc_id)
        embed_error = self._embed_chunks(chunks)
        indexed = self._index_chunks(doc_id, chunks)
        self._processed.add(doc_id)
        return IngestResult(
            doc_id=doc_id,
            chunks_created=len(chunks),
            chunks_indexed=indexed,
            error=embed_error,
        )

    def _embed_chunks(self, chunks: list[Any]) -> str | None:
        """Embed every chunk; return an error when none of them succeeded.

        Per-chunk warnings alone let a run where *every* embed failed finish as
        ``done`` with zero vectors indexed (docs/BUSINESS_LOGIC.md BL-10).
        """
        if self.embed_fn is None:
            return None
        failures = 0
        for chunk in chunks:
            try:
                chunk.embedding = self.embed_fn(chunk.text)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                logger.warning("Embed failed for %s: %s", chunk.chunk_id, exc)
        if failures and failures == len(chunks):
            return f"embed_failed_all ({failures}/{len(chunks)})"
        if failures:
            logger.warning("Embed failed for %d/%d chunks", failures, len(chunks))
        return None

    def _index_chunks(self, doc_id: str, chunks: list[Any]) -> int:
        embedded = [chunk for chunk in chunks if chunk.embedding]
        indexed = self.vector_store.upsert(embedded) if embedded else 0
        if self.fulltext_store is None:
            return indexed
        try:
            self.fulltext_store.index(chunks)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fulltext index failed for %s: %s", doc_id, exc)
        return indexed

    def run_once(self) -> list[IngestResult]:
        """Poll queued tasks first, then legacy unprocessed documents."""
        if self.task_store is not None:
            return self._run_task_batch()
        all_ids = self.doc_store.list_ids()
        pending = [did for did in all_ids if did not in self._processed]
        batch = pending[: self.config.batch_size]
        results: list[IngestResult] = []
        for doc_id in batch:
            results.append(self.process_doc(doc_id))
        return results

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

    def _run_task(self, task: Any) -> list[IngestResult]:
        """Index one claimed task's documents, then record its terminal status."""
        try:
            results = self._process_task_docs(task)
        except Exception as exc:  # noqa: BLE001
            self._finalize(task.id, IngestStatus.FAILED, type(exc).__name__)
            return []
        failed = [result for result in results if result.error]
        message = (failed[0].error or "failed") if failed else INDEXED_MESSAGE
        self._finalize(task.id, IngestStatus.FAILED if failed else IngestStatus.DONE, message)
        return results

    def _process_task_docs(self, task: Any) -> list[IngestResult]:
        """Index every document, refreshing the lease between documents."""
        results: list[IngestResult] = []
        for row in task.docs:
            results.append(self.process_doc(row["doc_id"]))
            self._queue.touch(task.id, owner=self.worker_id)
        return results

    def _finalize(self, task_id: str, status: IngestStatus, message: str) -> None:
        """Record the terminal status — bookkeeping must never look like failure.

        A stale requeue racing this worker puts the task back in QUEUED, which
        makes EXTRACTING → DONE invalid. That ValueError used to be caught by the
        batch handler and rewritten as FAILED, marking a *successful* task failed
        (docs/BUSINESS_LOGIC.md BL-06). Log it and leave the status alone.
        """
        try:
            self._queue.transition(task_id, status, message=message, owner=self.worker_id)
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


def _build_cli_worker() -> IngestWorker:
    """Wire the worker the way the docs promise: consuming the task queue.

    Two defects lived here (docs/BUSINESS_LOGIC.md BL-05): ``task_store`` was
    never passed, so ``run_once`` silently fell back to the legacy doc-store
    scan and queued tasks stayed ``queued`` forever; and an in-memory doc store
    means this process shares no data with the API, so the worker sees nothing.
    """
    from agentic_graphrag.config import get_config, get_settings, resolve_path
    from agentic_graphrag.stores.factory import create_offline_bundle

    cfg = get_config()
    settings = get_settings()
    bundle = create_offline_bundle(cfg=cfg, settings=settings)
    if not bundle.docs.durable:
        logger.warning(
            "Doc store %s is process-local: this worker cannot see documents uploaded "
            "through the API. Configure a file-backed doc store before relying on it.",
            type(bundle.docs).__name__,
        )
    return IngestWorker(
        doc_store=bundle.docs,
        vector_store=bundle.vector,
        fulltext_store=bundle.fulltext,
        task_store=IngestTaskStore(resolve_path(cfg.paths.processed_dir) / "ingest_tasks.jsonl"),
        config=IngestWorkerConfig(
            chunk_size_chars=cfg.knowledge.chunk_size_chars,
            chunk_overlap_chars=cfg.knowledge.chunk_overlap_chars,
        ),
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run ingest worker")
    parser.add_argument("--once", action="store_true", help="Single poll then exit")
    args = parser.parse_args()

    worker = _build_cli_worker()

    if args.once:
        results = worker.run_once()
        for r in results:
            print(f"  {r.doc_id}: {r.chunks_created} chunks, {r.chunks_indexed} indexed")
    else:
        worker.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            worker.stop()
