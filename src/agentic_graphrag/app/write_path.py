"""Knowledge write path wiring for the API process (BL-01 / BL-03).

* :func:`attach_write_path` gives ``QueryService`` a review executor, so a
  decision on ``POST /v1/review-queue/{id}/decision`` changes the graph.
* :func:`build_inline_worker` builds an ingest worker that shares the API's
  stores and graph — the only way uploads reach the graph when stores are
  process-local (offline). Enabled with ``AGR_INGEST_INLINE=1``.
* Read/write coupling goes through domain events (refactor §0-D): writers
  publish ``GraphCommitted`` / ``ReviewDecided``; :func:`subscribe_read_side`
  invalidates caches (→ ``IndexUpdated``) and refreshes the entity lexicon.
"""

from __future__ import annotations

import logging
import os

from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.core.events import (
    DomainEvent,
    EventBus,
    GraphCommitted,
    IndexUpdated,
    InProcessEventBus,
)
from agentic_graphrag.core.ports.capabilities import EntityLister
from agentic_graphrag.knowledge.graph_ingest import GraphIngestDeps, GraphIngestor
from agentic_graphrag.knowledge.ingest_worker import IngestWorker, IngestWorkerConfig
from agentic_graphrag.knowledge.review.executor import ReviewExecutor, ReviewExecutorDeps

logger = logging.getLogger(__name__)

INLINE_ENV = "AGR_INGEST_INLINE"
_INVALIDATE_RETRIES = 2
_ENTITY_SCAN_LIMIT = 100_000


def inline_worker_enabled() -> bool:
    return os.environ.get(INLINE_ENV, "").lower() in {"1", "true", "yes"}


def on_graph_commit(svc: QueryService) -> None:
    """Legacy entry point: publish ``GraphCommitted`` on the service's event bus."""
    events_for(svc).publish(GraphCommitted(source="direct"))


def events_for(svc: QueryService) -> EventBus:
    """The service's event bus, created and subscribed on first use."""
    bus = svc.events
    if bus is None:
        bus = InProcessEventBus()
        svc.events = bus
    if not svc._read_side_subscribed:
        subscribe_read_side(svc, bus)
        svc._read_side_subscribed = True
    return bus


def subscribe_read_side(svc: QueryService, bus: EventBus) -> None:
    """Read-side projections of write events (refactor §0-D).

    * ``GraphCommitted`` → bump the index version (invalidates retrieval/answer
      caches) and publish ``IndexUpdated``.
    * ``IndexUpdated`` → refresh the known-entity lexicon used by the matcher.
    """

    def invalidate(event: GraphCommitted) -> None:
        cache = svc.retrieval_cache
        version = cache.on_index_update() if cache is not None else 0
        bus.publish(IndexUpdated(tenant_id=event.tenant_id, version=int(version or 0)))

    def refresh_lexicon(_event: IndexUpdated) -> None:
        graph = svc.bundle.graph
        if not isinstance(graph, EntityLister):
            return
        names = [e.name for e in graph.list_entities(limit=_ENTITY_SCAN_LIMIT)]
        merged = list(dict.fromkeys([*svc.known_entities, *names]))
        # Mutate in place: executors built per query read this list.
        svc.known_entities[:] = merged

    # Stale answers are worse than a cold cache: invalidation is critical, and
    # if it still fails the fail-safe drops every cached retrieval / answer.
    bus.subscribe(
        GraphCommitted,
        invalidate,
        critical=True,
        retries=_INVALIDATE_RETRIES,
        on_failure=lambda event, exc: drop_caches(svc, event, exc),
    )
    bus.subscribe(IndexUpdated, refresh_lexicon)


def drop_caches(svc: QueryService, event: DomainEvent, exc: BaseException) -> None:
    """Fail-safe for a failed invalidation: clear caches and count the failure."""
    from agentic_graphrag.observability.metrics import get_metrics

    get_metrics().record_event_failure(f"{event.name}:invalidate:{type(exc).__name__}")
    cache = svc.retrieval_cache
    if cache is None:
        return
    for part in (cache.retrieval, cache.answers):
        part.clear()


def attach_write_path(svc: QueryService) -> None:
    """Give the service a review executor bound to its graph, queue and tasks."""
    svc.review_executor = ReviewExecutor(
        ReviewExecutorDeps(
            graph=svc.bundle.graph,
            review_queue=svc.review_queue,
            task_store=svc.ingest_tasks,
            events=events_for(svc),
        )
    )


def review_executor_for(svc: QueryService) -> ReviewExecutor | None:
    """The service's executor, re-bound to its *current* graph, queue and task store
    (tests and operators may swap those after construction)."""
    executor = svc.review_executor
    if executor is None:
        return None
    executor.graph = svc.bundle.graph
    executor.review_queue = svc.review_queue
    executor.task_store = svc.ingest_tasks
    return executor


def build_graph_ingestor(svc: QueryService) -> GraphIngestor:
    llm = None
    if svc.allow_llm:
        from agentic_graphrag.governance.budget import BudgetTracker

        llm = svc._build_llm(BudgetTracker(max_llm_calls=10_000, max_tokens=50_000_000))
    return GraphIngestor(
        GraphIngestDeps(
            graph=svc.bundle.graph,
            review_queue=svc.review_queue,
            llm=llm,
            events=events_for(svc),
        )
    )


def build_inline_worker(svc: QueryService) -> IngestWorker:
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


def start_inline_worker(svc: QueryService) -> IngestWorker | None:
    if not inline_worker_enabled() or svc.ingest_tasks is None:
        return None
    worker = build_inline_worker(svc)
    worker.start()
    logger.info("Inline ingest worker started (uploads → index → knowledge graph)")
    return worker
