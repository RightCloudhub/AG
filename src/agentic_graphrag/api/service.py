"""Query application service — wires stores + agent loop for the API."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentic_graphrag.api.schemas import QueryRequest, QueryResultData
from agentic_graphrag.api.service_helpers import (
    ANSWER_CACHE_TTL_SECONDS,
    _chain_to_data,
    _entities_from_triples,
    _load_triples,
    build_executor_for_service,
    build_llm_for_service,
)
from agentic_graphrag.api.service_query import (
    execute_run_query,
)
from agentic_graphrag.api.service_query import (
    stream_query_events as _stream_query_events,
)
from agentic_graphrag.config import AppConfig, Settings, get_config, get_settings, resolve_path
from agentic_graphrag.generation.audit_store import AuditStore
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph
from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore
from agentic_graphrag.knowledge.review.queue import ReviewExecutor, ReviewQueue, ReviewType
from agentic_graphrag.llm.budget import BudgetTracker
from agentic_graphrag.llm.budget_policy import MultiLevelBudget
from agentic_graphrag.llm.provider import LLMProvider, MockLLMProvider
from agentic_graphrag.retrieval.cache import RetrievalCache
from agentic_graphrag.stores.factory import (
    StoreBundle,
    create_live_bundle,
    create_offline_bundle,
)

if TYPE_CHECKING:
    from agentic_graphrag.knowledge.ingest_worker import IngestWorker
    from agentic_graphrag.knowledge.incremental import IncrementalUpdater
    from agentic_graphrag.knowledge.schema_check import SchemaDefinition

# Re-exports for tests / public helpers
__all__ = [
    "QueryService",
    "build_default_service",
    "_chain_to_data",
    "_entities_from_triples",
    "_load_triples",
]


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


@dataclass
class QueryService:
    """Stateful query runner (holds StoreBundle for process lifetime)."""

    cfg: AppConfig
    settings: Settings
    bundle: StoreBundle
    allow_llm: bool = False
    known_entities: list[str] = field(default_factory=list)
    enable_triage: bool = True
    enable_cache: bool = True
    audit_store: AuditStore | None = None
    review_queue: ReviewQueue | None = None
    review_executor: ReviewExecutor | None = None
    retrieval_cache: RetrievalCache | None = None
    multi_budget: MultiLevelBudget | None = None
    ingest_tasks: IngestTaskStore | None = None
    # BL-01: runtime write path — worker that consumes the ingest task queue
    # and (when graph + schema + llm are wired) extracts triples into the
    # knowledge graph. ``_owns_worker`` tracks whether this service started
    # the background thread so it can stop it on close().
    schema: SchemaDefinition | None = None
    incremental_updater: IncrementalUpdater | None = None
    ingest_worker: IngestWorker | None = None
    _owns_worker: bool = False
    # Legacy field retained for tests that monkeypatch it; query paths no longer
    # hold this lock across agent/SSE lifetimes (see service_query / service_stream).
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def create_offline(
        cls,
        *,
        seed_triples: str | Path = "data/processed/seed_triples.jsonl",
        cfg: AppConfig | None = None,
        settings: Settings | None = None,
    ) -> QueryService:
        """Offline default for CI / local: memory graph + seed triples + Mock LLM."""
        cfg = cfg or get_config()
        settings = settings or get_settings()
        bundle = create_offline_bundle(cfg=cfg, settings=settings)
        return cls._from_bundle(
            bundle, cfg=cfg, settings=settings, seed_triples=seed_triples, allow_llm=False
        )

    @classmethod
    def create_live(
        cls,
        *,
        seed_triples: str | Path = "data/processed/seed_triples.jsonl",
        cfg: AppConfig | None = None,
        settings: Settings | None = None,
        allow_memory_graph_fallback: bool = True,
        load_seed: bool = False,
    ) -> QueryService:
        """Live Neo4j + Qdrant backends (opt-in via ``AGR_USE_LIVE_STORES=1``)."""
        cfg = cfg or get_config()
        settings = settings or get_settings()
        bundle = create_live_bundle(
            cfg=cfg,
            settings=settings,
            allow_memory_graph_fallback=allow_memory_graph_fallback,
        )
        return cls._from_bundle(
            bundle,
            cfg=cfg,
            settings=settings,
            seed_triples=seed_triples,
            allow_llm=False,
            load_seed=load_seed,
        )

    @classmethod
    def _from_bundle(
        cls,
        bundle: StoreBundle,
        *,
        cfg: AppConfig,
        settings: Settings,
        seed_triples: str | Path,
        allow_llm: bool,
        load_seed: bool = True,
    ) -> QueryService:
        from agentic_graphrag.llm.budget_policy import BudgetLimits

        triples = _load_triples(resolve_path(seed_triples)) if load_seed else []
        if triples:
            load_triples_into_graph(bundle.graph, triples, clear_first=True)
        # Build per-tenant budget overrides from config (ENT-05).
        tenant_overrides: dict[str, BudgetLimits] = {}
        for tid, tcfg in cfg.tenants.items():
            tenant_overrides[tid] = BudgetLimits(
                max_llm_calls=tcfg.max_llm_calls or 10_000,
                max_tokens=tcfg.max_tokens or 5_000_000,
                max_cost_units=tcfg.max_cost_units or 1000.0,
            )
        review_queue = ReviewQueue(resolve_path(cfg.paths.processed_dir) / "review_queue.jsonl")
        # BL-01: load schema (best-effort — tests/sandboxes may not ship one)
        # so the runtime ingest worker and IncrementalUpdater can enforce the
        # P2-KG-02 gate. Missing schema ⇒ graph write path is disabled.
        schema = cls._try_load_schema(cfg)
        # BL-01 / BL-03: IncrementalUpdater routes conflicts to the review queue
        # so /v1/review-queue can list and decide them.
        incremental_updater = None
        if schema is not None:
            from agentic_graphrag.knowledge.incremental import IncrementalUpdater

            incremental_updater = IncrementalUpdater(
                bundle.graph,
                schema=schema,
                confidence_threshold=cfg.knowledge.extract_confidence_threshold,
                review_queue=review_queue,
            )
        svc = cls(
            cfg=cfg,
            settings=settings,
            bundle=bundle,
            allow_llm=allow_llm,
            known_entities=_entities_from_triples(triples) if triples else [],
            audit_store=AuditStore(resolve_path(cfg.paths.processed_dir) / "audit_chains.jsonl"),
            review_queue=review_queue,
            review_executor=ReviewExecutor(
                bundle.graph,
                schema=schema,
                confidence_threshold=cfg.knowledge.extract_confidence_threshold,
            ),
            retrieval_cache=RetrievalCache(
                cache_dir=resolve_path(cfg.paths.cache_dir) / "retrieval",
                answer_ttl_seconds=ANSWER_CACHE_TTL_SECONDS,
            ),
            multi_budget=MultiLevelBudget(tenant_overrides=tenant_overrides),
            ingest_tasks=IngestTaskStore(
                resolve_path(cfg.paths.processed_dir) / "ingest_tasks.jsonl"
            ),
            schema=schema,
            incremental_updater=incremental_updater,
        )
        svc._build_ingest_worker()
        return svc

    @staticmethod
    def _try_load_schema(cfg: AppConfig) -> SchemaDefinition | None:
        """BL-01: best-effort schema load. Returns None if missing or invalid."""
        try:
            from agentic_graphrag.knowledge.schema_check import load_schema

            return load_schema(resolve_path(cfg.knowledge.schema_path))
        except Exception:  # noqa: BLE001 — schema is optional at runtime
            return None

    def _build_ingest_worker(self) -> None:
        """BL-01: construct (but do not start) the background ingest worker.

        Worker is started lazily by ``start_background_workers`` (env-gated).
        When ``AGR_ALLOW_LLM=1`` is set with a real API key, an LLM provider
        is built so the worker can extract triples; otherwise it only does
        vector/fulltext indexing (no graph writes).
        """
        from agentic_graphrag.knowledge.ingest_worker import (
            IngestWorker,
            IngestWorkerConfig,
        )

        llm: LLMProvider | None = None
        # Only build a real LLM — MockLLMProvider would emit fake triples and
        # poison the graph. ``build_llm_for_service`` returns Mock when
        # ``allow_llm`` is False, so we guard explicitly.
        if (
            self.allow_llm
            and self.schema is not None
            and self.settings.llm_api_key
            and "your-key" not in self.settings.llm_api_key
        ):
            try:
                budget = BudgetTracker(max_llm_calls=10_000, max_tokens=10_000_000)
                real_llm = build_llm_for_service(
                    allow_llm=True,
                    settings=self.settings,
                    cfg=self.cfg,
                    budget=budget,
                )
                # Reject if the helper fell back to Mock (e.g. missing key edge cases).
                if not isinstance(real_llm, MockLLMProvider):
                    llm = real_llm
            except Exception:  # noqa: BLE001 — worker still useful for indexing without LLM
                llm = None
        embed_fn = getattr(self.bundle, "embed_fn", None)
        # Only wire graph write path when we have everything needed.
        graph = self.bundle.graph if (self.schema is not None and llm is not None) else None
        schema = self.schema if llm is not None else None
        self.ingest_worker = IngestWorker(
            doc_store=self.bundle.docs,
            vector_store=self.bundle.vector,
            fulltext_store=self.bundle.fulltext,
            embed_fn=embed_fn,
            task_store=self.ingest_tasks,
            graph=graph,
            schema=schema,
            llm=llm,
            incremental_updater=self.incremental_updater,
            config=IngestWorkerConfig(
                chunk_size_chars=self.cfg.knowledge.chunk_size_chars,
                chunk_overlap_chars=self.cfg.knowledge.chunk_overlap_chars,
                extract_confidence_threshold=self.cfg.knowledge.extract_confidence_threshold,
                extract_max_attempts=self.cfg.knowledge.extract_max_attempts,
                extract_retry_base_delay_seconds=self.cfg.knowledge.extract_retry_base_delay_seconds,
                extract_journal_path=str(resolve_path(self.cfg.knowledge.extract_journal_path)),
                extract_quarantine_path=str(resolve_path(self.cfg.knowledge.extract_quarantine_path)),
            ),
        )

    def start_background_workers(self) -> None:
        """BL-01: start the ingest worker thread. Idempotent.

        Called by the API lifespan when ``AGR_INGEST_WORKER=1``. Off by default
        so existing tests and CI smoke flows keep using the synchronous path
        (uploads persist documents + queue a task; worker consumes them only
        when explicitly enabled).

        Rebuilds the worker if ``allow_llm`` was toggled after construction
        (e.g. by ``build_default_service`` setting ``allow_llm=True`` after
        ``_from_bundle`` already ran with ``allow_llm=False``). Without this
        rebuild the LLM-based graph write path would be silently disabled.
        """
        # Bug fix: _from_bundle builds the worker before allow_llm is set by
        # build_default_service. Rebuild now if allow_llm flipped to True.
        if self.ingest_worker is not None and self.allow_llm and not self._owns_worker:
            cur = self.ingest_worker
            if cur.llm is None:
                self._build_ingest_worker()
        if self.ingest_worker is None or self._owns_worker:
            return
        self.ingest_worker.start()
        self._owns_worker = True

    def stop_background_workers(self) -> None:
        """BL-01: stop the ingest worker thread if this service started it."""
        if self.ingest_worker is None or not self._owns_worker:
            return
        self.ingest_worker.stop()
        self._owns_worker = False

    def close(self) -> None:
        self.stop_background_workers()
        self.bundle.close()

    def run_query(
        self,
        req: QueryRequest,
        *,
        tenant_id: str = "default",
        user_id: str = "anonymous",
    ) -> QueryResultData:
        return execute_run_query(self, req, tenant_id=tenant_id, user_id=user_id)

    def stream_query_events(
        self,
        req: QueryRequest,
        *,
        tenant_id: str = "default",
        user_id: str = "anonymous",
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """Yield SSE (event, payload) pairs for progressive UI (P3-PERF-06)."""
        return _stream_query_events(self, req, tenant_id=tenant_id, user_id=user_id)

    def submit_feedback(
        self,
        query_id: str,
        *,
        accurate: bool,
        reason: str = "",
        user_id: str = "",
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """FR-OP-03: feedback linked to reasoning chain → badcase/review queue."""
        payload = {
            "query_id": query_id,
            "accurate": accurate,
            "reason": reason,
            "user_id": user_id,
        }
        if self.review_queue is not None and not accurate:
            item = self.review_queue.enqueue(
                ReviewType.FEEDBACK,
                payload,
                confidence=0.0,
                tenant_id=tenant_id,
            )
            payload["review_id"] = item.id
        self._attach_feedback_to_audit(query_id, payload)
        return payload

    def _attach_feedback_to_audit(self, query_id: str, payload: dict[str, Any]) -> None:
        if self.audit_store is None:
            return
        chain = self.audit_store.get(query_id)
        if chain is None:
            return
        meta = dict(chain.get("metadata") or {})
        feedbacks = list(meta.get("feedback") or [])
        feedbacks.append(payload)
        meta["feedback"] = feedbacks
        chain["metadata"] = meta
        self.audit_store.save(chain)

    def _build_executor(self):
        return build_executor_for_service(
            bundle=self.bundle,
            cfg=self.cfg,
            settings=self.settings,
            allow_llm=self.allow_llm,
            known_entities=self.known_entities,
            retrieval_cache=self.retrieval_cache,
            enable_cache=self.enable_cache,
        )

    def _build_llm(self, budget: BudgetTracker) -> LLMProvider | MockLLMProvider:
        return build_llm_for_service(
            allow_llm=self.allow_llm,
            settings=self.settings,
            cfg=self.cfg,
            budget=budget,
        )


def build_default_service() -> QueryService:
    """Factory used by FastAPI lifespan.

    Defaults to **offline** (memory graph + seed + MockLLM) so CI/local smoke
    stays deterministic.

    Env flags (independent):
    - ``AGR_ALLOW_LLM=1`` — use real LLM when ``LLM_API_KEY`` is set (stores unchanged)
    - ``AGR_USE_LIVE_STORES=1`` — Neo4j + Qdrant instead of in-memory seed graph
    """
    settings = get_settings()
    cfg = get_config()
    if _env_flag("AGR_USE_LIVE_STORES"):
        svc = QueryService.create_live(cfg=cfg, settings=settings)
    else:
        svc = QueryService.create_offline(cfg=cfg, settings=settings)
    if (
        _env_flag("AGR_ALLOW_LLM")
        and settings.llm_api_key
        and "your-key" not in settings.llm_api_key
    ):
        svc.allow_llm = True
    return svc
