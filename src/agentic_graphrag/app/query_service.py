"""Query application service — wires stores + agent loop for the API."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.app.contracts import QueryRequest, QueryResultData
from agentic_graphrag.app.query_run import (
    execute_run_query,
)
from agentic_graphrag.app.query_run import (
    stream_query_events as _stream_query_events,
)
from agentic_graphrag.app.wiring import (
    ANSWER_CACHE_TTL_SECONDS,
    _chain_to_data,
    _entities_from_triples,
    _load_seed_graph,
    _load_triples,
    build_executor_for_service,
    build_llm_for_service,
)
from agentic_graphrag.bootstrap.profile import RuntimeProfile
from agentic_graphrag.config import (
    AppConfig,
    Settings,
    get_config,
    get_settings,
    resolve_path,
    runtime_dir,
)
from agentic_graphrag.core.events import EventBus
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.audit_store import AuditStore
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.governance.budget_policy import MultiLevelBudget
from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore
from agentic_graphrag.knowledge.review.executor import ReviewExecutor
from agentic_graphrag.knowledge.review.queue import ReviewQueue
from agentic_graphrag.llm.provider import MockLLMProvider
from agentic_graphrag.retrieval.cache import RetrievalCache
from agentic_graphrag.stores.factory import (
    StoreBundle,
    create_live_bundle,
    create_offline_bundle,
)

# Re-exports for tests / public helpers
__all__ = [
    "QueryService",
    "build_default_service",
    "_chain_to_data",
    "_entities_from_triples",
    "_load_triples",
]

logger = logging.getLogger(__name__)


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
    retrieval_cache: RetrievalCache | None = None
    multi_budget: MultiLevelBudget | None = None
    ingest_tasks: IngestTaskStore | None = None
    # BL-03: applies review decisions to the graph (set by ``attach_write_path``).
    review_executor: ReviewExecutor | None = None
    # Runtime profile name (offline | live | hybrid | eval) when built via bootstrap.
    profile: str = "offline"
    # In-process domain event bus (GraphCommitted / IndexUpdated / ReviewDecided).
    events: EventBus | None = None
    _read_side_subscribed: bool = False
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
        allow_memory_graph_fallback: bool = False,
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
    def create_for_profile(
        cls,
        rp: RuntimeProfile,
        *,
        seed_triples: str | Path = "data/processed/seed_triples.jsonl",
        cfg: AppConfig | None = None,
        settings: Settings | None = None,
    ) -> QueryService:
        """Wire a service for a resolved :class:`~agentic_graphrag.bootstrap.RuntimeProfile`."""
        from agentic_graphrag.bootstrap import build_stores

        cfg = cfg or get_config()
        settings = settings or get_settings()
        bundle = build_stores(rp, cfg=cfg, settings=settings)
        svc = cls._from_bundle(
            bundle,
            cfg=cfg,
            settings=settings,
            seed_triples=seed_triples,
            allow_llm=rp.allow_llm,
            load_seed=rp.caps.load_seed_graph,
        )
        svc.enable_cache = rp.caps.answer_cache
        svc.profile = rp.profile.value
        return svc

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
        from agentic_graphrag.governance.budget_policy import BudgetLimits

        triples = _load_triples(resolve_path(seed_triples)) if load_seed else []
        if triples:
            # Gated by default now (BL-07): seed triples go through the same
            # schema + confidence gate as every other ingestion path.
            _load_seed_graph(bundle.graph, triples)
        # Build per-tenant budget overrides from config (ENT-05).
        tenant_overrides: dict[str, BudgetLimits] = {}
        for tid, tcfg in cfg.tenants.items():
            tenant_overrides[tid] = BudgetLimits(
                max_llm_calls=tcfg.max_llm_calls or 10_000,
                max_tokens=tcfg.max_tokens or 5_000_000,
                max_cost_units=tcfg.max_cost_units or 1000.0,
            )
        svc = cls(
            cfg=cfg,
            settings=settings,
            bundle=bundle,
            allow_llm=allow_llm,
            known_entities=_entities_from_triples(triples) if triples else [],
            audit_store=AuditStore(runtime_dir(cfg) / "audit_chains.jsonl"),
            review_queue=ReviewQueue(runtime_dir(cfg) / "review_queue.jsonl"),
            retrieval_cache=RetrievalCache(
                cache_dir=resolve_path(cfg.paths.cache_dir) / "retrieval",
                answer_ttl_seconds=ANSWER_CACHE_TTL_SECONDS,
            ),
            multi_budget=MultiLevelBudget(tenant_overrides=tenant_overrides),
            ingest_tasks=IngestTaskStore(runtime_dir(cfg) / "ingest_tasks.jsonl"),
        )
        from agentic_graphrag.app.write_path import attach_write_path

        attach_write_path(svc)
        return svc

    def close(self) -> None:
        self.bundle.close()

    def run_query(
        self,
        req: QueryRequest,
        *,
        tenant_id: str = "default",
        user_id: str = "anonymous",
    ) -> QueryResultData:
        return execute_run_query(self, req, tenant_id=tenant_id, user_id=user_id)

    def run_query_chain(
        self,
        req: QueryRequest,
        *,
        tenant_id: str = "default",
        user_id: str = "anonymous",
    ) -> ReasoningChain:
        """Same production path as :meth:`run_query`, returning the full chain."""
        from agentic_graphrag.app.query_run import execute_query_chain

        return execute_query_chain(self, req, tenant_id=tenant_id, user_id=user_id)

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
        from agentic_graphrag.app.feedback import submit_feedback

        return submit_feedback(
            self, query_id, accurate=accurate, reason=reason, user_id=user_id, tenant_id=tenant_id
        )

    def _build_executor(self) -> Executor:
        return build_executor_for_service(
            bundle=self.bundle,
            cfg=self.cfg,
            settings=self.settings,
            allow_llm=self.allow_llm,
            known_entities=self.known_entities,
            retrieval_cache=self.retrieval_cache,
            enable_cache=self.enable_cache,
        )

    def _build_llm(self, budget: BudgetTracker) -> LLMClient | MockLLMProvider:
        return build_llm_for_service(
            allow_llm=self.allow_llm,
            settings=self.settings,
            cfg=self.cfg,
            budget=budget,
        )


def build_default_service() -> QueryService:
    """Factory used by FastAPI lifespan, driven by the runtime profile.

    ``AGR_PROFILE`` picks one of ``offline`` (default), ``live``, ``hybrid`` or
    ``eval``; legacy ``AGR_USE_LIVE_STORES=1`` maps to ``live``. ``AGR_ALLOW_LLM=1``
    enables the real LLM when the profile allows it and ``LLM_API_KEY`` is set.
    Invalid combinations or unreachable stores fail at startup.
    """
    from agentic_graphrag.bootstrap import resolve_runtime_profile

    settings = get_settings()
    cfg = get_config()
    rp = resolve_runtime_profile(llm_api_key=settings.llm_api_key)
    return QueryService.create_for_profile(rp, cfg=cfg, settings=settings)
