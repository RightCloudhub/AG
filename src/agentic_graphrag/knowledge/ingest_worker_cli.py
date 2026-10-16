"""``python -m agentic_graphrag.knowledge.ingest_worker`` entry point.

Wires the worker the way the docs promise: consuming the upload task queue
(BL-05) and writing extracted facts into the knowledge graph (BL-01). With
``AGR_USE_LIVE_STORES=1`` it shares Neo4j/Qdrant with the API; offline, the
graph and vectors are process-local, so prefer the API's inline worker
(``AGR_INGEST_INLINE=1``) there.
"""

from __future__ import annotations

import argparse
import logging
import time

from agentic_graphrag.knowledge.graph_ingest import GraphIngestDeps, GraphIngestor
from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore
from agentic_graphrag.knowledge.ingest_worker import IngestWorker, IngestWorkerConfig
from agentic_graphrag.knowledge.review.queue import ReviewQueue

logger = logging.getLogger(__name__)


def build_cli_worker() -> IngestWorker:
    from agentic_graphrag.bootstrap import build_stores, resolve_runtime_profile
    from agentic_graphrag.config import get_config, get_settings, resolve_path

    cfg = get_config()
    settings = get_settings()
    rp = resolve_runtime_profile(llm_api_key=settings.llm_api_key)
    bundle = build_stores(rp, cfg=cfg, settings=settings)
    if not bundle.docs.durable:
        logger.warning(
            "Doc store %s is process-local: this worker cannot see documents uploaded "
            "through the API. Configure a file-backed doc store before relying on it.",
            type(bundle.docs).__name__,
        )
    processed = resolve_path(cfg.paths.processed_dir)
    ingestor = GraphIngestor(
        GraphIngestDeps(
            graph=bundle.graph, review_queue=ReviewQueue(processed / "review_queue.jsonl")
        )
    )
    return IngestWorker(
        doc_store=bundle.docs,
        vector_store=bundle.vector,
        fulltext_store=bundle.fulltext,
        task_store=IngestTaskStore(processed / "ingest_tasks.jsonl"),
        config=IngestWorkerConfig(
            chunk_size_chars=cfg.knowledge.chunk_size_chars,
            chunk_overlap_chars=cfg.knowledge.chunk_overlap_chars,
        ),
        graph_ingestor=ingestor,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run ingest worker")
    parser.add_argument("--once", action="store_true", help="Single poll then exit")
    args = parser.parse_args(argv)
    worker = build_cli_worker()
    if args.once:
        for r in worker.run_once():
            facts = (r.graph or {}).get("accepted", 0)
            print(
                f"  {r.doc_id}: {r.chunks_created} chunks, {r.chunks_indexed} indexed, "
                f"{facts} graph facts"
            )
        return
    worker.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        worker.stop()
