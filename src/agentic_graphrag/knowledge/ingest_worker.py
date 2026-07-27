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
from typing import TYPE_CHECKING, Any

from agentic_graphrag.knowledge.ingest import chunk_document
from agentic_graphrag.knowledge.ingest_tasks import IngestStatus, IngestTaskStore
from agentic_graphrag.stores.interfaces import DocStore, VectorStore

if TYPE_CHECKING:
    from agentic_graphrag.knowledge.extract_types import ExtractFn
    from agentic_graphrag.knowledge.incremental import IncrementalUpdater
    from agentic_graphrag.knowledge.schema_check import SchemaDefinition
    from agentic_graphrag.llm.provider import LLMProvider
    from agentic_graphrag.stores.interfaces import GraphStore

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    doc_id: str
    chunks_created: int = 0
    chunks_indexed: int = 0
    error: str | None = None
    triples_accepted: int = 0
    triples_rejected: int = 0
    relations_upserted: int = 0
    conflicts_review: int = 0


@dataclass
class IngestWorkerConfig:
    """Tuning knobs for the background ingest loop."""

    poll_interval_seconds: float = 5.0
    batch_size: int = 10
    chunk_size_chars: int = 1200
    chunk_overlap_chars: int = 150
    # BL-01: extract → graph load knobs (only used when graph + schema + llm wired).
    extract_confidence_threshold: float = 0.5
    extract_max_attempts: int = 3
    extract_retry_base_delay_seconds: float = 0.0
    extract_journal_path: str | None = None
    extract_quarantine_path: str | None = None


class IngestWorker:
    """Process unindexed documents from the doc store into vector/fulltext indexes.

    Tracks which doc_ids have been processed via an in-memory set (sufficient
    for single-process deployments; upgrade to persistent journal for multi-node).

    BL-01: when ``graph``, ``schema`` and (``llm`` or ``extract_fn``) are wired,
    each task also runs the extract pipeline and writes accepted triples into
    the graph via ``IncrementalUpdater`` (so conflicts route to ReviewQueue
    instead of silently overwriting). This closes the runtime write-path gap
    between ``POST /v1/docs`` and the knowledge graph.
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
        graph: GraphStore | None = None,
        schema: SchemaDefinition | None = None,
        llm: LLMProvider | None = None,
        extract_fn: ExtractFn | None = None,
        incremental_updater: IncrementalUpdater | None = None,
    ) -> None:
        self.doc_store = doc_store
        self.vector_store = vector_store
        self.fulltext_store = fulltext_store
        self.embed_fn = embed_fn
        self.config = config or IngestWorkerConfig()
        self.task_store = task_store
        # BL-01: graph write path. ``incremental_updater`` is preferred when
        # available because it routes conflicts to the review queue; otherwise
        # we fall back to a direct ``load_triples_into_graph`` upsert.
        self.graph = graph
        self.schema = schema
        self.llm = llm
        self.extract_fn = extract_fn
        self.incremental_updater = incremental_updater
        self._processed: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def graph_write_enabled(self) -> bool:
        """Whether this worker will extract triples and write them to the graph.

        Public so the API lifespan can log the effective mode without reaching
        into private state. True only when ``graph``, ``schema`` and an LLM
        (or ``extract_fn``) are all wired.
        """
        return (
            self.graph is not None
            and self.schema is not None
            and (self.llm is not None or self.extract_fn is not None)
        )

    def process_doc(self, doc_id: str) -> IngestResult:
        """Process a single document: chunk → embed → upsert.

        BL-01: when graph + schema + llm are wired, also runs the extract
        pipeline and writes accepted triples to the graph.
        """
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
        # BL-10: report failure when embeddings were required but none succeeded.
        # Previously every chunk failing embed_fn still yielded result.error=None
        # and the task was marked `done` with zero indexed chunks.
        if self.embed_fn is not None and indexed == 0 and chunks:
            return IngestResult(
                doc_id=doc_id,
                chunks_created=len(chunks),
                chunks_indexed=0,
                error="all_embeddings_failed",
            )
        result = IngestResult(doc_id=doc_id, chunks_created=len(chunks), chunks_indexed=indexed)
        # BL-01: extract → graph load. Run after index so a graph failure does
        # not discard the vector/fulltext work that already succeeded.
        if self.graph_write_enabled:
            try:
                self._extract_and_load_graph(doc, chunks, result)
            except Exception as exc:  # noqa: BLE001 — graph write failure must not mask index success
                logger.error(
                    "extract → graph load failed for %s (vector/fulltext index unaffected): %s",
                    doc_id,
                    exc,
                )
                if result.error is None:
                    result.error = f"graph_load_failed: {type(exc).__name__}"
        self._processed.add(doc_id)
        return result

    def _extract_and_load_graph(
        self, doc: Any, chunks: list[Any], result: IngestResult
    ) -> None:
        """BL-01: run extract pipeline on chunks and write triples to the graph.

        Uses ``IncrementalUpdater.apply_batch`` when wired (so conflicts land
        in the review queue); otherwise falls back to a direct
        ``load_triples_into_graph`` upsert with the schema gate enforced.
        """
        from agentic_graphrag.knowledge.extract_pipeline import run_extract_pipeline
        from agentic_graphrag.knowledge.extract_types import RetryPolicy

        assert self.schema is not None  # guarded by graph_write_enabled
        retry = RetryPolicy(
            max_attempts=self.config.extract_max_attempts,
            base_delay_seconds=self.config.extract_retry_base_delay_seconds,
        )
        tenant_id = doc.tenant_id or str(doc.metadata.get("tenant_id") or "")
        batch_id = f"ingest-{doc.doc_id}-{int(time.time())}"
        pipe = run_extract_pipeline(
            chunks,
            self.schema,
            llm=self.llm,
            extract_fn=self.extract_fn,
            confidence_threshold=self.config.extract_confidence_threshold,
            retry=retry,
            journal_path=self.config.extract_journal_path,
            quarantine_path=self.config.extract_quarantine_path,
            batch_id=batch_id,
        )
        result.triples_accepted = len(pipe.accepted)
        result.triples_rejected = len(pipe.rejected)
        if not pipe.accepted:
            return
        # Stamp tenant onto triples so the graph write is tenant-scoped.
        for triple in pipe.accepted:
            if not triple.attributes:
                triple.attributes = {}
            triple.attributes.setdefault("tenant_id", tenant_id)
        if self.incremental_updater is not None:
            batch = self.incremental_updater.apply_batch(pipe.accepted, batch_id=batch_id)
            result.relations_upserted = batch.accepted
            result.conflicts_review = batch.conflicts_review
            logger.info(
                "ingest graph load doc=%s accepted=%d conflicts_review=%d",
                doc.doc_id,
                batch.accepted,
                batch.conflicts_review,
            )
            return
        # Fallback: direct upsert (still schema-gated).
        from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph

        assert self.graph is not None  # guarded by graph_write_enabled
        stats = load_triples_into_graph(
            self.graph,
            pipe.accepted,
            clear_first=False,
            schema=self.schema,
            confidence_threshold=self.config.extract_confidence_threshold,
        )
        result.relations_upserted = int(
            stats.get("relations_upserted", stats.get("relationships", 0))
        )
        logger.info(
            "ingest graph load doc=%s relations_upserted=%d",
            doc.doc_id,
            result.relations_upserted,
        )

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
            # BL-06: requeue stale EXTRACTING tasks before polling.
            requeued = self.task_store.requeue_stale()
            if requeued:
                logger.info("Requeued %d stale extracting tasks", requeued)
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

    from agentic_graphrag.config import build_llm_provider, get_config, get_settings, resolve_path
    from agentic_graphrag.knowledge.schema_check import load_schema
    from agentic_graphrag.llm.budget import BudgetTracker
    from agentic_graphrag.stores.factory import create_offline_bundle

    parser = argparse.ArgumentParser(description="Run ingest worker")
    parser.add_argument("--once", action="store_true", help="Single poll then exit")
    parser.add_argument(
        "--no-graph",
        action="store_true",
        help="Skip extract → graph load (legacy behavior: vector/fulltext only)",
    )
    args = parser.parse_args()

    cfg = get_config()
    settings = get_settings()
    bundle = create_offline_bundle(cfg=cfg, settings=settings)

    # BL-05: wire up the task store so the worker consumes the API's queue
    # (previously the documented command ignored the queue entirely).
    task_store_path = resolve_path(cfg.paths.processed_dir) / "ingest_tasks.jsonl"
    task_store = IngestTaskStore(task_store_path)

    # BL-05: use the bundle's embed_fn if available so chunks get embeddings.
    embed_fn = getattr(bundle, "embed_fn", None)

    # BL-01: wire graph + schema + llm so the worker also extracts triples
    # and writes them into the knowledge graph (previously the runtime
    # upload→graph pathway did not exist; only the CLI agr-build-graph
    # could populate the graph).
    schema = None
    llm = None
    if not args.no_graph:
        try:
            schema = load_schema(resolve_path(cfg.knowledge.schema_path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("schema load failed; graph write disabled: %s", exc)
        if schema is not None and settings.llm_api_key and "your-key" not in settings.llm_api_key:
            try:
                budget = BudgetTracker(max_llm_calls=10_000, max_tokens=10_000_000)
                llm = build_llm_provider(
                    budget=budget,
                    cache_dir=resolve_path(cfg.paths.cache_dir) / "llm",
                    settings=settings,
                    cfg=cfg,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM provider build failed; graph write disabled: %s", exc)
        else:
            logger.info(
                "LLM_API_KEY not set; graph write disabled (use --no-graph to silence)."
            )

    worker = IngestWorker(
        doc_store=bundle.docs,
        vector_store=bundle.vector,
        fulltext_store=bundle.fulltext,
        embed_fn=embed_fn,
        task_store=task_store,
        graph=bundle.graph if schema is not None and llm is not None else None,
        schema=schema if llm is not None else None,
        llm=llm,
        config=IngestWorkerConfig(
            chunk_size_chars=cfg.knowledge.chunk_size_chars,
            chunk_overlap_chars=cfg.knowledge.chunk_overlap_chars,
            extract_confidence_threshold=cfg.knowledge.extract_confidence_threshold,
            extract_max_attempts=cfg.knowledge.extract_max_attempts,
            extract_retry_base_delay_seconds=cfg.knowledge.extract_retry_base_delay_seconds,
            extract_journal_path=str(resolve_path(cfg.knowledge.extract_journal_path)),
            extract_quarantine_path=str(resolve_path(cfg.knowledge.extract_quarantine_path)),
        ),
    )

    if args.once:
        results = worker.run_once()
        for r in results:
            print(
                f"  {r.doc_id}: {r.chunks_created} chunks, {r.chunks_indexed} indexed, "
                f"{r.triples_accepted} triples accepted, {r.relations_upserted} relations upserted"
            )
    else:
        worker.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            worker.stop()
