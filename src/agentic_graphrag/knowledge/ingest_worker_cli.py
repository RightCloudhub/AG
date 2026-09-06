"""CLI wiring for the ingest worker (kept out of ``ingest_worker`` for the
file-size gate). Entry point: ``python -m agentic_graphrag.knowledge.ingest_worker``.
"""

from __future__ import annotations

import logging
from typing import Any

from agentic_graphrag.knowledge.graph_ingest import GraphIngestPipeline
from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore
from agentic_graphrag.knowledge.ingest_worker import IngestWorker, IngestWorkerConfig

logger = logging.getLogger(__name__)


def build_cli_worker() -> IngestWorker:
    """Wire the worker the way the docs promise: consuming the task queue.

    Two defects lived here (docs/BUSINESS_LOGIC.md BL-05): ``task_store`` was
    never passed, so ``run_once`` silently fell back to the legacy doc-store
    scan and queued tasks stayed ``queued`` forever; and an in-memory doc store
    means this process shares no data with the API, so the worker sees nothing.

    The graph pipeline (BL-01) closes the upload → graph gap: extracted triples
    are gated, conflict-checked, and applied to the worker's graph store; with
    no live LLM configured, tasks land in REVIEW instead.
    """
    from agentic_graphrag.config import get_config, get_settings, resolve_path
    from agentic_graphrag.knowledge.incremental import IncrementalUpdater
    from agentic_graphrag.knowledge.review.queue import ReviewQueue
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
    review_queue = ReviewQueue(resolve_path(cfg.paths.processed_dir) / "review_queue.jsonl")
    updater = IncrementalUpdater(
        bundle.graph,
        review_log=resolve_path(cfg.paths.processed_dir) / "review_log.jsonl",
        review_queue=review_queue,
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
        graph_pipeline=GraphIngestPipeline(
            updater=updater,
            llm=_live_llm_if_configured(settings, cfg),
            chunk_size_chars=cfg.knowledge.chunk_size_chars,
            chunk_overlap_chars=cfg.knowledge.chunk_overlap_chars,
        ),
    )


def _live_llm_if_configured(settings: Any, cfg: Any) -> Any:
    """A real LLM provider when a non-placeholder key exists, else ``None``."""
    key = getattr(settings, "llm_api_key", "") or ""
    if not key.strip() or "your-key" in key:
        return None
    try:
        from agentic_graphrag.config import build_llm_provider

        return build_llm_provider(settings=settings, cfg=cfg)
    except Exception:  # noqa: BLE001
        logger.warning("LLM provider construction failed; graph extraction stays offline")
        return None
