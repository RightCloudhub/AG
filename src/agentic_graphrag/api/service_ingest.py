"""Knowledge write path wiring for the API process (BL-01 / BL-03).

* :func:`attach_write_path` gives ``QueryService`` a review executor, so a
  decision on ``POST /v1/review-queue/{id}/decision`` changes the graph.
* :func:`build_inline_worker` builds an ingest worker that shares the API's
  stores and graph — the only way uploads reach the graph when stores are
  process-local (offline). Enabled with ``AGR_INGEST_INLINE=1``.
* :func:`on_graph_commit` keeps the query side coherent after any graph write:
  retrieval/answer caches are invalidated and new entity names become known to
  the entity matcher.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from agentic_graphrag.knowledge.graph_ingest import GraphIngestDeps, GraphIngestor
from agentic_graphrag.knowledge.ingest_worker import IngestWorker, IngestWorkerConfig
from agentic_graphrag.knowledge.review.executor import ReviewExecutor, ReviewExecutorDeps

logger = logging.getLogger(__name__)

INLINE_ENV = "AGR_INGEST_INLINE"
_ENTITY_SCAN_LIMIT = 100_000


def inline_worker_enabled() -> bool:
    return os.environ.get(INLINE_ENV, "").lower() in {"1", "true", "yes"}


def on_graph_commit(svc: Any) -> None:
    """Invalidate caches and refresh the known-entity lexicon after a graph write."""
    cache = getattr(svc, "retrieval_cache", None)
    if cache is not None:
        cache.on_index_update()
    lister = getattr(svc.bundle.graph, "list_entities", None)
    if not callable(lister):
        return
    names = [e.name for e in lister(limit=_ENTITY_SCAN_LIMIT)]
    merged = list(dict.fromkeys([*svc.known_entities, *names]))
    # Mutate in place: executors built per query read this list.
    svc.known_entities[:] = merged


def attach_write_path(svc: Any) -> None:
    """Give the service a review executor bound to its graph, queue and tasks."""
    svc.review_executor = ReviewExecutor(
        ReviewExecutorDeps(
            graph=svc.bundle.graph,
            review_queue=svc.review_queue,
            task_store=svc.ingest_tasks,
            on_commit=lambda: on_graph_commit(svc),
        )
    )


def review_executor_for(svc: Any) -> ReviewExecutor | None:
    """The service's executor, re-bound to its *current* graph, queue and task store
    (tests and operators may swap those after construction)."""
    executor = getattr(svc, "review_executor", None)
    if executor is None:
        return None
    executor.graph = svc.bundle.graph
    executor.review_queue = svc.review_queue
    executor.task_store = svc.ingest_tasks
    return executor


def build_graph_ingestor(svc: Any) -> GraphIngestor:
    llm = None
    if svc.allow_llm:
        from agentic_graphrag.llm.budget import BudgetTracker

        llm = svc._build_llm(BudgetTracker(max_llm_calls=10_000, max_tokens=50_000_000))
    return GraphIngestor(
        GraphIngestDeps(
            graph=svc.bundle.graph,
            review_queue=svc.review_queue,
            llm=llm,
            on_commit=lambda: on_graph_commit(svc),
        )
    )


def build_inline_worker(svc: Any) -> IngestWorker:
    """Ingest worker sharing the API's stores (docs, vectors, BM25, graph)."""
    return IngestWorker(
        doc_store=svc.bundle.docs,
        vector_store=svc.bundle.vector,
        fulltext_store=svc.bundle.fulltext,
        task_store=svc.ingest_tasks,
        config=IngestWorkerConfig(
            poll_interval_seconds=float(os.environ.get("AGR_INGEST_POLL_SECONDS", "2")),
            chunk_size_chars=svc.cfg.knowledge.chunk_size_chars,
            chunk_overlap_chars=svc.cfg.knowledge.chunk_overlap_chars,
        ),
        graph_ingestor=build_graph_ingestor(svc),
    )


def start_inline_worker(svc: Any) -> IngestWorker | None:
    if not inline_worker_enabled() or svc.ingest_tasks is None:
        return None
    worker = build_inline_worker(svc)
    worker.start()
    logger.info("Inline ingest worker started (uploads → index → knowledge graph)")
    return worker
