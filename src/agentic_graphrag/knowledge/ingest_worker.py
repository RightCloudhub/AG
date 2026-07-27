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
from agentic_graphrag.knowledge.schema_check import SchemaDefinition, load_default_schema
from agentic_graphrag.stores.interfaces import DocStore, GraphStore, VectorStore

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
        graph_store: GraphStore | None = None,
        schema: SchemaDefinition | None = None,
        llm: Any | None = None,
        config: IngestWorkerConfig | None = None,
    ) -> None:
        self.doc_store = doc_store
        self.vector_store = vector_store
        self.fulltext_store = fulltext_store
        self.embed_fn = embed_fn
        self.config = config or IngestWorkerConfig()
        self.task_store = task_store
        self.graph_store = graph_store
        self.schema = schema or load_default_schema()
        self.llm = llm
        self._processed: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def process_doc(self, doc_id: str) -> IngestResult:
        """Chunk → embed → upsert → (optional) graph build."""
        doc = self.doc_store.get(doc_id)
        if doc is None:
            return IngestResult(doc_id=doc_id, error="not_found")
        chunks = chunk_document(
            doc, chunk_size=self.config.chunk_size_chars, overlap=self.config.chunk_overlap_chars,
        )
        if not chunks:
            self._processed.add(doc_id)
            return IngestResult(doc_id=doc_id)
        self._embed_chunks(chunks)
        indexed = self._index_chunks(doc_id, chunks)
        embed_failed = indexed < len(chunks) if len(chunks) > 0 else False
        if embed_failed:
            logger.warning("Partial embedding failure for %s: %d chunks, %d indexed", doc_id, len(chunks), indexed)

        # Optionally build graph from extracted triples (BL-01)
        graph_ok = True
        if self.graph_store is not None:
            try:
                self._build_graph_from_chunks(doc_id, chunks)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Graph build failed for %s: %s", doc_id, exc)
                graph_ok = False

        self._processed.add(doc_id)
        result = IngestResult(doc_id=doc_id, chunks_created=len(chunks), chunks_indexed=indexed)
        errors = []
        if embed_failed:
            errors.append("partial_embedding_failure")
        if not graph_ok:
            errors.append("graph_build_failed")
        if errors:
            result.error = "; ".join(errors)
        return result

    def _build_graph_from_chunks(self, doc_id: str, chunks: list[Any]) -> None:
        """Extract triples from chunks and upsert into graph store (BL-01).

        Uses the LLM if available (``extract_from_chunks``), otherwise falls
        back to a mock extraction from chunk metadata (offline/demo mode).
        """
        from agentic_graphrag.knowledge.extract_core import extract_from_chunks
        from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph
        from agentic_graphrag.stores.interfaces import ChunkRecord

        if self.llm is not None:
            accepted, _rejected = extract_from_chunks(
                [c for c in chunks if isinstance(c, ChunkRecord)],
                self.schema,
                self.llm,
            )
        else:
            # Offline path: extract mock triples from chunk metadata for demo
            accepted = self._mock_extract(doc_id, chunks)

        if accepted:
            load_triples_into_graph(
                self.graph_store,
                accepted,
                clear_first=False,
                schema=self.schema,
            )

    def _mock_extract(self, doc_id: str, chunks: list[Any]) -> list[Any]:
        """Produce demo triples from chunk metadata when no LLM is available."""
        from agentic_graphrag.knowledge.schema_check import EntityMention, Triple

        triples: list[Triple] = []
        for chunk in chunks:
            meta = getattr(chunk, "metadata", None) or {}
            entities = meta.get("entities", [])
            relations = meta.get("relations", [])
            for ent in entities:
                triples.append(
                    Triple(
                        head=EntityMention(name=str(ent.get("name", "")), type=str(ent.get("type", "Unknown"))),
                        relation="MENTIONED_IN",
                        tail=EntityMention(name=doc_id, type="Document"),
                        confidence=0.5,
                        source_doc_id=doc_id,
                        source_chunk_id=chunk.chunk_id if hasattr(chunk, "chunk_id") else "",
                    )
                )
            for rel in relations:
                triples.append(
                    Triple(
                        head=EntityMention(name=str(rel.get("head", "")), type=str(rel.get("head_type", "Unknown"))),
                        relation=str(rel.get("type", "RELATED_TO")),
                        tail=EntityMention(name=str(rel.get("tail", "")), type=str(rel.get("tail_type", "Unknown"))),
                        confidence=float(rel.get("confidence", 0.5)),
                        source_doc_id=doc_id,
                        source_chunk_id=chunk.chunk_id if hasattr(chunk, "chunk_id") else "",
                    )
                )
        return triples

    def _embed_chunks(self, chunks: list[Any]) -> None:
        if self.embed_fn is None:
            return
        for chunk in chunks:
            try:
                chunk.embedding = self.embed_fn(chunk.text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Embed failed for %s: %s", chunk.chunk_id, exc)
                chunk.embedding = None

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
        for task in self.task_store.pending_or_stale(self.config.batch_size):
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

    from agentic_graphrag.config import get_config, get_settings, resolve_path
    from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore
    from agentic_graphrag.stores.factory import create_offline_bundle

    parser = argparse.ArgumentParser(description="Run ingest worker")
    parser.add_argument("--once", action="store_true", help="Single poll then exit")
    args = parser.parse_args()

    cfg = get_config()
    settings = get_settings()
    bundle = create_offline_bundle(cfg=cfg, settings=settings)

    # Share the same task_store JSONL path as the API process so queued tasks
    # are visible to this worker (BL-05 fix).
    task_store = IngestTaskStore(
        resolve_path(cfg.paths.processed_dir) / "ingest_tasks.jsonl"
    )

    worker = IngestWorker(
        doc_store=bundle.docs,
        vector_store=bundle.vector,
        fulltext_store=bundle.fulltext,
        task_store=task_store,
        graph_store=bundle.graph,
        schema=load_default_schema(),
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
