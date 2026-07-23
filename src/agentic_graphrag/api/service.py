"""Query application service — wires stores + agent loop for the API."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
from agentic_graphrag.knowledge.review.queue import ReviewQueue, ReviewType
from agentic_graphrag.llm.budget import BudgetTracker
from agentic_graphrag.llm.budget_policy import MultiLevelBudget
from agentic_graphrag.llm.provider import LLMProvider, MockLLMProvider
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
    retrieval_cache: RetrievalCache | None = None
    multi_budget: MultiLevelBudget | None = None
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
        triples = _load_triples(resolve_path(seed_triples)) if load_seed else []
        if triples:
            load_triples_into_graph(bundle.graph, triples, clear_first=True)
        return cls(
            cfg=cfg,
            settings=settings,
            bundle=bundle,
            allow_llm=allow_llm,
            known_entities=_entities_from_triples(triples) if triples else [],
            audit_store=AuditStore(resolve_path(cfg.paths.processed_dir) / "audit_chains.jsonl"),
            review_queue=ReviewQueue(resolve_path(cfg.paths.processed_dir) / "review_queue.jsonl"),
            retrieval_cache=RetrievalCache(
                cache_dir=resolve_path(cfg.paths.cache_dir) / "retrieval",
                answer_ttl_seconds=ANSWER_CACHE_TTL_SECONDS,
            ),
            multi_budget=MultiLevelBudget(),
        )

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
