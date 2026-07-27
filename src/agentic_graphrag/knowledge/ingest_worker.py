"""Background ingest worker: chunk → embed → index uploaded docs (ENT-06)."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentic_graphrag.knowledge.ingest import chunk_document
from agentic_graphrag.knowledge.ingest_tasks import IngestStatus, IngestTaskStore
from agentic_graphrag.stores.interfaces import DocStore, GraphStore, VectorStore

if TYPE_CHECKING:
    from agentic_graphrag.knowledge.schema_check import SchemaDefinition
    from agentic_graphrag.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


def _load_schema(path: Path) -> SchemaDefinition:
    """Lazy-load SchemaDefinition to avoid pulling yaml at import time."""
    from agentic_graphrag.knowledge.schema_check import load_schema

    return load_schema(path)


@dataclass
class IngestResult:
    doc_id: str
    chunks_created: int = 0
    chunks_indexed: int = 0
    error: str | None = None
    triples_accepted: int = 0
    triples_rejected: int = 0
    graph_extraction_error: str | None = None


@dataclass
class IngestWorkerConfig:
    """Tuning knobs for the background ingest loop."""

    poll_interval_seconds: float = 5.0
    batch_size: int = 10
    chunk_size_chars: int = 1200
    chunk_overlap_chars: int = 150


class IngestWorker:
    """Process unindexed documents from doc store into vector/fulltext indexes."""

    # BL-06: stale extracting tasks are requeued if older than this threshold.
    STALE_EXTRACTING_SECONDS = 30 * 60

    def __init__(
        self,
        *,
        doc_store: DocStore,
        vector_store: VectorStore,
        fulltext_store: Any | None = None,
        embed_fn: Any | None = None,
        task_store: IngestTaskStore | None = None,
        config: IngestWorkerConfig | None = None,
        # BL-01: optional graph extraction dependencies
        graph_store: GraphStore | None = None,
        llm_provider: LLMProvider | None = None,
        schema: SchemaDefinition | None = None,
        extract_confidence_threshold: float = 0.5,
        journal_path: str | Path | None = None,
        quarantine_path: str | Path | None = None,
    ) -> None:
        self.doc_store = doc_store
        self.vector_store = vector_store
        self.fulltext_store = fulltext_store
        self.embed_fn = embed_fn
        self.config = config or IngestWorkerConfig()
        self.task_store = task_store
        self.graph_store = graph_store
        self.llm_provider = llm_provider
        self.schema = schema
        self.extract_confidence_threshold = extract_confidence_threshold
        self.journal_path = journal_path
        self.quarantine_path = quarantine_path
        self._processed: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._extraction_enabled = bool(self.graph_store and self.llm_provider and self.schema)

    def process_doc(self, doc_id: str) -> IngestResult:
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

        result = IngestResult(
            doc_id=doc_id,
            chunks_created=len(chunks),
            chunks_indexed=indexed,
        )

        if self._extraction_enabled:
            self._extract_and_build(chunks, result)
        self._processed.add(doc_id)
        return result

    def _extract_and_build(self, chunks: list[Any], result: IngestResult) -> None:
        try:
            from agentic_graphrag.knowledge.extract_pipeline import run_extract_pipeline
            from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph

            pipeline_result = run_extract_pipeline(
                chunks,  # type: ignore[arg-type]
                self.schema,  # type: ignore[arg-type]
                llm=self.llm_provider,
                confidence_threshold=self.extract_confidence_threshold,
                journal_path=self.journal_path,
                quarantine_path=self.quarantine_path,
            )
            accepted = pipeline_result.accepted or []
            result.triples_accepted = len(accepted)
            result.triples_rejected = len(pipeline_result.rejected or [])
            if accepted:
                load_triples_into_graph(
                    self.graph_store,
                    accepted,  # type: ignore[arg-type]
                    clear_first=False,
                    schema=self.schema,  # type: ignore[arg-type]
                    confidence_threshold=self.extract_confidence_threshold,
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("Graph extraction failed for %s: %s", result.doc_id, exc)
            result.graph_extraction_error = type(exc).__name__

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
        self._recover_stale_tasks()
        if self.task_store is not None:
            return self._run_task_batch()
        all_ids = self.doc_store.list_ids()
        pending = [did for did in all_ids if did not in self._processed]
        batch = pending[: self.config.batch_size]
        results: list[IngestResult] = []
        for doc_id in batch:
            results.append(self.process_doc(doc_id))
        return results

    def _recover_stale_tasks(self) -> None:
        """Requeue stale EXTRACTING tasks via public IngestTaskStore API."""
        if self.task_store is None:
            return
        threshold = time.time() - self.STALE_EXTRACTING_SECONDS
        try:
            stale = self.task_store.list_by_status(IngestStatus.EXTRACTING.value)
            for task in stale:
                if task.updated_at >= threshold:
                    continue
                try:
                    self.task_store.transition(
                        task.id,
                        IngestStatus.QUEUED,
                        message="Recovered from stale EXTRACTING state",
                    )
                    logger.info(
                        "Recovered stale EXTRACTING task %s (age %.0fs)",
                        task.id,
                        time.time() - task.updated_at,
                    )
                except (KeyError, ValueError):
                    pass  # race with normal transition; skip silently
        except Exception:  # noqa: BLE001 — recovery must not break the worker loop
            logger.warning("Failed to recover stale EXTRACTING tasks")

    def _run_task_batch(self) -> list[IngestResult]:
        assert self.task_store is not None
        results: list[IngestResult] = []
        for task in self.task_store.pending(self.config.batch_size):
            self.task_store.transition(task.id, IngestStatus.EXTRACTING)
            try:
                task_results = [self.process_doc(row["doc_id"]) for row in task.docs]
                results.extend(task_results)
                failed = [r for r in task_results if r.error]
                status = IngestStatus.FAILED if failed else IngestStatus.DONE
                message = failed[0].error or "failed" if failed else "Indexed successfully"
                self.task_store.transition(task.id, status, message=message)
            except Exception as exc:  # noqa: BLE001
                self.task_store.transition(task.id, IngestStatus.FAILED, message=type(exc).__name__)
        return results

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ingest-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
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


if __name__ == "__main__":
    import argparse

    from agentic_graphrag.config import build_llm_provider, get_config, get_settings, resolve_path
    from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore
    from agentic_graphrag.stores.factory import create_offline_bundle

    parser = argparse.ArgumentParser(description="Run ingest worker")
    parser.add_argument("--once", action="store_true", help="Single poll then exit")
    args = parser.parse_args()

    cfg = get_config()
    settings = get_settings()
    bundle = create_offline_bundle(cfg=cfg, settings=settings)

    llm = build_llm_provider(settings=settings, cfg=cfg) if settings.llm_api_key else None
    schema_path = resolve_path(cfg.paths.config_dir) / "schema" / "domain_v0.yaml"
    schema = _load_schema(schema_path) if llm and schema_path.exists() else None
    _proc = resolve_path(cfg.paths.processed_dir)
    _jpath = _proc / "extraction_journal.jsonl" if llm else None
    _qpath = _proc / "extraction_quarantine.jsonl" if llm else None

    worker = IngestWorker(
        doc_store=bundle.docs,
        vector_store=bundle.vector,
        fulltext_store=bundle.fulltext,
        embed_fn=getattr(cfg.knowledge, "embed_fn", None),
        task_store=IngestTaskStore(_proc / "ingest_tasks.jsonl"),
        config=IngestWorkerConfig(
            chunk_size_chars=cfg.knowledge.chunk_size_chars,
            chunk_overlap_chars=cfg.knowledge.chunk_overlap_chars,
        ),
        graph_store=bundle.graph,
        llm_provider=llm,
        schema=schema,
        extract_confidence_threshold=getattr(cfg.knowledge, "confidence_threshold", 0.5),
        journal_path=_jpath,
        quarantine_path=_qpath,
    )

    if args.once:
        for r in worker.run_once():
            print(f"  {r.doc_id}: {r.chunks_created} chunks, {r.chunks_indexed} indexed")
    else:
        worker.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            worker.stop()
