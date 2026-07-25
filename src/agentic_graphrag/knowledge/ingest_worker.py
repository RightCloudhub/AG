"""Background ingest worker: chunk → embed → index uploaded docs (ENT-06).

Usage (standalone)::

    python -m agentic_graphrag.knowledge.ingest_worker --once

Or use ``IngestWorker`` as a background thread in the API process.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from agentic_graphrag.knowledge.ingest import chunk_document
from agentic_graphrag.knowledge.ingest_tasks import IngestStatus, IngestTaskStore
from agentic_graphrag.stores.interfaces import DocStore, VectorStore

logger = logging.getLogger(__name__)


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
    ) -> None:
        self.doc_store = doc_store
        self.vector_store = vector_store
        self.fulltext_store = fulltext_store
        self.embed_fn = embed_fn
        self.config = config or IngestWorkerConfig()
        self.task_store = task_store
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
        self._embed_chunks(chunks)
        indexed = self._index_chunks(doc_id, chunks)
        self._processed.add(doc_id)
        return IngestResult(doc_id=doc_id, chunks_created=len(chunks), chunks_indexed=indexed)

    def _embed_chunks(self, chunks: list[Any]) -> None:
        if self.embed_fn is None:
            return
        for chunk in chunks:
            try:
                chunk.embedding = self.embed_fn(chunk.text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Embed failed for %s: %s", chunk.chunk_id, exc)

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
        assert self.task_store is not None
        results: list[IngestResult] = []
        for task in self.task_store.pending(self.config.batch_size):
            self.task_store.transition(task.id, IngestStatus.EXTRACTING)
            try:
                task_results = [self.process_doc(row["doc_id"]) for row in task.docs]
                results.extend(task_results)
                failed = [result for result in task_results if result.error]
                status = IngestStatus.FAILED if failed else IngestStatus.DONE
                message = failed[0].error or "failed" if failed else "Indexed successfully"
                self.task_store.transition(task.id, status, message=message)
            except Exception as exc:  # noqa: BLE001
                self.task_store.transition(task.id, IngestStatus.FAILED, message=type(exc).__name__)
        return results

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
    import argparse

    from agentic_graphrag.config import get_config, get_settings
    from agentic_graphrag.stores.factory import create_offline_bundle

    parser = argparse.ArgumentParser(description="Run ingest worker")
    parser.add_argument("--once", action="store_true", help="Single poll then exit")
    args = parser.parse_args()

    cfg = get_config()
    settings = get_settings()
    bundle = create_offline_bundle(cfg=cfg, settings=settings)

    worker = IngestWorker(
        doc_store=bundle.docs,
        vector_store=bundle.vector,
        fulltext_store=bundle.fulltext,
        config=IngestWorkerConfig(
            chunk_size_chars=cfg.knowledge.chunk_size_chars,
            chunk_overlap_chars=cfg.knowledge.chunk_overlap_chars,
        ),
    )

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
