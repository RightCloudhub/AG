"""In-process ingest worker bootstrap (BL-01) — kept out of ``app.py`` for the
file-size gate. See :func:`start_ingest_worker_if_enabled`.
"""

from __future__ import annotations

import logging
from typing import Any

from agentic_graphrag.api.service import QueryService

logger = logging.getLogger(__name__)


def start_ingest_worker_if_enabled(svc: QueryService) -> Any | None:
    """Opt-in in-process ingest worker (``AGR_INGEST_WORKER=1``), or ``None``.

    The default stays off — the API does not auto-start workers. With the flag,
    uploads flow chunk → index → extract → conflict → graph inside this very
    process, so even the single-process trial deployment (in-memory stores)
    gets the runtime upload → graph path (BL-01) without a separate worker.
    """
    import os

    if os.environ.get("AGR_INGEST_WORKER", "").lower() not in {"1", "true", "yes"}:
        return None
    from agentic_graphrag.api.service_helpers import build_llm_for_service
    from agentic_graphrag.config import resolve_path
    from agentic_graphrag.knowledge.graph_ingest import GraphIngestPipeline
    from agentic_graphrag.knowledge.incremental import IncrementalUpdater
    from agentic_graphrag.knowledge.ingest_worker import IngestWorker, IngestWorkerConfig
    from agentic_graphrag.llm.budget import BudgetTracker

    cfg = svc.cfg
    budget = BudgetTracker(max_llm_calls=10_000, max_tokens=5_000_000)
    llm = build_llm_for_service(
        allow_llm=svc.allow_llm, settings=svc.settings, cfg=cfg, budget=budget
    )
    updater = IncrementalUpdater(
        svc.bundle.graph,
        review_log=resolve_path(cfg.paths.processed_dir) / "review_log.jsonl",
        review_queue=svc.review_queue,
    )
    worker = IngestWorker(
        doc_store=svc.bundle.docs,
        vector_store=svc.bundle.vector,
        fulltext_store=svc.bundle.fulltext,
        task_store=svc.ingest_tasks,
        config=IngestWorkerConfig(
            chunk_size_chars=cfg.knowledge.chunk_size_chars,
            chunk_overlap_chars=cfg.knowledge.chunk_overlap_chars,
        ),
        graph_pipeline=GraphIngestPipeline(
            updater=updater,
            llm=llm if svc.allow_llm else None,
            chunk_size_chars=cfg.knowledge.chunk_size_chars,
            chunk_overlap_chars=cfg.knowledge.chunk_overlap_chars,
        ),
    )
    worker.start()
    logger.info("In-process ingest worker started (AGR_INGEST_WORKER=1)")
    return worker
