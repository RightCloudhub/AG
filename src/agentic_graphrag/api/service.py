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
            bundle,
            cfg=cfg,
            settings=settings,
            seed_triples=seed_triples,
            allow_llm=False,
            clear_graph=True,
        )

    @classmethod
    def create_live(
        cls,
        *,
        seed_triples: str | Path = "data/processed/seed_triples.jsonl",
        cfg: AppConfig | None = None,
        settings: Settings | None = None,
        allow_memory_graph_fallback: bool | None = None,
    ) -> QueryService:
        """Live stores: Neo4j + Qdrant. Fallback only when env allows."""
        cfg = cfg or get_config()
        settings = settings or get_settings()
        if allow_memory_graph_fallback is None:
            allow_memory_graph_fallback = _env_flag("AGR_LIVE_GRAPH_FALLBACK")
        fail_closed = _env_flag("AGR_LIVE_FAIL_CLOSED")
        bundle = create_live_bundle(
            cfg=cfg,
            settings=settings,
            allow_memory_graph_fallback=allow_memory_graph_fallback and not fail_closed,
        )
        # Seed only memory graphs; Neo4j is expected to already hold the pilot graph.
        seed = seed_triples if bundle.graph_backend.value == "memory" else ""
        return cls._from_bundle(
            bundle,
            cfg=cfg,
            settings=settings,
            seed_triples=seed,
            allow_llm=False,
            clear_graph=bundle.graph_backend.value == "memory",
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
        clear_graph: bool,
    ) -> QueryService:
        triples: list[Any] = []
        if seed_triples:
            triples = _load_triples(resolve_path(seed_triples))
            if triples:
                load_triples_into_graph(bundle.graph, triples, clear_first=clear_graph)
        return cls(
            cfg=cfg,
            settings=settings,
            bundle=bundle,
            allow_llm=allow_llm,
            known_entities=_entities_from_triples(triples),
            audit_store=AuditStore(
                resolve_path(cfg.paths.processed_dir) / "audit_chains.jsonl"
            ),
            review_queue=ReviewQueue(
                resolve_path(cfg.paths.processed_dir) / "review_queue.jsonl"
            ),
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

    def get_audit_for_tenant(
        self, query_id: str, *, tenant_id: str
    ) -> dict[str, Any] | None:
        """Load audit row only when tenant matches (P4-REL-01 AuthZ)."""
        if self.audit_store is None:
            return None
        row = self.audit_store.get(query_id)
        if row is None:
            return None
        meta = row.get("metadata") or {}
        row_tenant = str(meta.get("tenant_id") or "default")
        if row_tenant != tenant_id:
            return None
        return row

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
        chain = self.get_audit_for_tenant(query_id, tenant_id=tenant_id)
        if chain is None and self.audit_store is not None:
            # Unknown or cross-tenant: refuse to attach feedback (IDOR guard).
            if self.audit_store.get(query_id) is not None:
                raise PermissionError("feedback denied for tenant")
        payload = {
            "query_id": query_id,
            "accurate": accurate,
            "reason": reason,
            "user_id": user_id,
            "tenant_id": tenant_id,
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


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


def build_default_service() -> QueryService:
    """Factory used by FastAPI lifespan.

    Defaults to **offline** (memory graph + seed + MockLLM) so CI/local smoke
    stays deterministic. Opt into live LLM with ``AGR_ALLOW_LLM=1`` when a real
    ``LLM_API_KEY`` is configured. Opt into Neo4j/Qdrant with ``AGR_LIVE_STORES=1``.
    """
    settings = get_settings()
    cfg = get_config()
    if _env_flag("AGR_LIVE_STORES"):
        svc = QueryService.create_live(cfg=cfg, settings=settings)
    else:
        svc = QueryService.create_offline(cfg=cfg, settings=settings)
    allow = _env_flag("AGR_ALLOW_LLM")
    if allow and settings.llm_api_key and "your-key" not in settings.llm_api_key:
        svc.allow_llm = True
    if _env_flag("AGR_DEGRADE_TO_FAST_PATH"):
        svc.enable_triage = True
        # Prefer Fast Path under degrade: still allow escalation via triage.
        svc.cfg = cfg
    return svc
