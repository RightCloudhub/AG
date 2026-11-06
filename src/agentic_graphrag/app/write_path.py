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
from dataclasses import replace

from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.core.events import (
    DomainEvent,
    EventBus,
    GraphCommitted,
    IndexUpdated,
    InProcessEventBus,
)
from agentic_graphrag.core.ports.capabilities import EntityLister
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.knowledge.graph_ingest import GraphIngestor
from agentic_graphrag.knowledge.ingest_worker import IngestWorker
from agentic_graphrag.knowledge.review.executor import ReviewExecutor, ReviewExecutorDeps
from agentic_graphrag.knowledge.worker_wiring import batch_embedder, worker_config

logger = logging.getLogger(__name__)

INLINE_ENV = "AGR_INGEST_INLINE"
_INVALIDATE_RETRIES = 2
_ENTITY_SCAN_LIMIT = 100_000


def inline_worker_enabled() -> bool:
    return os.environ.get(INLINE_ENV, "").lower() in {"1", "true", "yes"}


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

    get_metrics().record_event_failure(
        f"{event.name}:invalidate:{type(exc).__name__}", tenant_id=event.tenant_id
    )
    cache = svc.retrieval_cache
    if cache is None:
        return
    for part in (cache.retrieval, cache.answers):
        part.clear()


def attach_write_path(svc: QueryService) -> None:
    """Give the service a review executor bound to its graph, queue and tasks."""
    from agentic_graphrag.app.graph_wiring import entity_index_for, entity_space, schema_for_service
    from agentic_graphrag.app.knowledge_writes import publish_task

    opened = bool(svc.knowledge_base and svc.knowledge_base.build_mode == "open")
    svc.review_executor = ReviewExecutor(
        ReviewExecutorDeps(
            graph=svc.bundle.graph,
            review_queue=svc.review_queue,
            task_store=svc.ingest_tasks,
            events=events_for(svc),
            docs=svc.bundle.docs,
            schema=schema_for_service(svc),
            entity_index=entity_index_for(svc) if opened else None,
            embedding_space=entity_space(svc) if opened else "",
            before_done=(lambda task_id: publish_task(svc, task_id))
            if svc.knowledge_base
            else None,
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
    executor.docs = svc.bundle.docs
    return executor


def build_graph_ingestor(svc: QueryService, *, llm: LLMClient | None = None) -> GraphIngestor:
    from agentic_graphrag.app.graph_wiring import graph_ingestor

    return graph_ingestor(svc, llm=llm)


def build_inline_worker(
    svc: QueryService, *, route_tasks: bool = True, worker_id: str | None = None
) -> IngestWorker:
    """Ingest worker sharing the API's stores (docs, vectors, BM25, graph)."""
    config = worker_config(svc.cfg, live=svc.allow_llm)
    config.poll_interval_seconds = float(os.environ.get("AGR_INGEST_POLL_SECONDS", "2"))
    from agentic_graphrag.app.knowledge_writes import publish_task, run_scoped_task
    from agentic_graphrag.app.summary_queue import process_summary_work

    local = replace(svc, allow_llm=False) if route_tasks and svc.knowledge_base is None else svc
    llm = (
        local._build_llm(
            BudgetTracker(
                max_llm_calls=svc.cfg.indexing.max_llm_calls,
                max_tokens=svc.cfg.indexing.max_tokens,
            )
        )
        if local.allow_llm
        else None
    )

    return IngestWorker(
        doc_store=svc.bundle.docs,
        vector_store=svc.bundle.vector,
        fulltext_store=svc.bundle.fulltext,
        task_store=svc.ingest_tasks,
        config=config,
        embed_many=llm.embed_many if llm else batch_embedder(svc.cfg, svc.settings, live=False),
        on_commit=lambda: events_for(svc).publish(GraphCommitted(source="index")),
        graph_ingestor=build_graph_ingestor(local, llm=llm),
        before_done=(lambda task: publish_task(svc, task.id)) if svc.knowledge_base else None,
        worker_id=worker_id,
        task_runner=(lambda task, owner: run_scoped_task(svc, task, owner))
        if route_tasks and svc.knowledge_base is None
        else None,
        background_work=(lambda: process_summary_work(svc))
        if route_tasks and svc.knowledge_base is None
        else None,
    )


def start_inline_worker(svc: QueryService) -> IngestWorker | None:
    if not inline_worker_enabled() or svc.ingest_tasks is None:
        return None
    worker = build_inline_worker(svc)
    worker.start()
    logger.info("Inline ingest worker started (uploads → index → knowledge graph)")
    return worker
