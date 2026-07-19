"""Query application service — wires stores + agent loop for the API."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import run_query
from agentic_graphrag.api.errors import BUDGET_EXCEEDED, INTERNAL_ERROR, ApiError
from agentic_graphrag.api.schemas import QueryRequest, QueryResultData
from agentic_graphrag.api.sse import (
    EVENT_ANSWER,
    EVENT_CACHE_HIT,
    EVENT_ERROR,
    EVENT_TRIAGE,
)
from agentic_graphrag.config import (
    AppConfig,
    Settings,
    build_llm_provider,
    get_config,
    get_settings,
    resolve_path,
)
from agentic_graphrag.generation.audit_store import AuditStore
from agentic_graphrag.generation.trace import ReasoningChain
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph
from agentic_graphrag.knowledge.review.queue import ReviewQueue, ReviewType
from agentic_graphrag.knowledge.schema_check import Triple
from agentic_graphrag.llm.budget import BudgetExceeded, BudgetTracker
from agentic_graphrag.llm.budget_policy import MultiLevelBudget
from agentic_graphrag.llm.provider import LLMProvider, MockLLMProvider
from agentic_graphrag.observability.metrics import QueryMetrics, get_metrics
from agentic_graphrag.observability.trace import get_tracer, span
from agentic_graphrag.retrieval.cache import RetrievalCache
from agentic_graphrag.retrieval.fulltext import FulltextRetriever
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.retrieval.vector import VectorRetriever
from agentic_graphrag.stores.factory import StoreBundle, create_offline_bundle


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
        triples = _load_triples(resolve_path(seed_triples))
        if triples:
            load_triples_into_graph(bundle.graph, triples, clear_first=True)
        known = _entities_from_triples(triples)
        cache = RetrievalCache(
            cache_dir=resolve_path(cfg.paths.cache_dir) / "retrieval",
            answer_ttl_seconds=3600.0,
        )
        audit = AuditStore(resolve_path(cfg.paths.processed_dir) / "audit_chains.jsonl")
        review = ReviewQueue(resolve_path(cfg.paths.processed_dir) / "review_queue.jsonl")
        return cls(
            cfg=cfg,
            settings=settings,
            bundle=bundle,
            allow_llm=False,
            known_entities=known,
            audit_store=audit,
            review_queue=review,
            retrieval_cache=cache,
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
        t0 = time.perf_counter()
        timeout_override = int(req.timeout_ms / 1000) if req.timeout_ms is not None else None
        guard_cfg = GuardrailConfig.from_app_config(
            self.cfg,
            max_hops=req.max_hops,
            query_timeout_seconds=timeout_override,
        )
        budget = (
            self.multi_budget.query_tracker()
            if self.multi_budget
            else guard_cfg.budget_tracker()
        )
        tracer = get_tracer()
        trace_ctx = tracer.start(tenant_id=tenant_id, user_id=user_id)

        # Answer cache
        if self.enable_cache and self.retrieval_cache and not req.force_agentic:
            hit = self.retrieval_cache.get_answer(req.question)
            if hit is not None:
                data = QueryResultData.model_validate(hit)
                data.metadata = {**(data.metadata or {}), "cache_hit": True}
                return data

        if self.multi_budget:
            try:
                self.multi_budget.check_and_reserve(
                    tenant_id=tenant_id, user_id=user_id, estimated_calls=1
                )
            except BudgetExceeded as exc:
                get_metrics().record_budget_trip()
                raise ApiError(
                    BUDGET_EXCEEDED,
                    "Query budget exceeded",
                    status_code=429,
                    details={"reason": str(exc)},
                ) from exc

        with self._lock:
            executor = self._build_executor()
            llm = self._build_llm(budget)
            try:
                with span(trace_ctx, "run_query", question=req.question[:120]):
                    chain = run_query(
                        req.question,
                        executor,
                        llm if self.allow_llm else None,
                        guard_cfg=guard_cfg,
                        budget=budget,
                        allow_llm=self.allow_llm,
                        force_agentic=req.force_agentic,
                        enable_triage=self.enable_triage and not req.force_agentic,
                        known_entities=self.known_entities,
                    )
            except BudgetExceeded as exc:
                get_metrics().record_budget_trip()
                raise ApiError(
                    BUDGET_EXCEEDED,
                    "Query budget exceeded",
                    status_code=429,
                    details={"reason": str(exc)},
                ) from exc
            except Exception as exc:  # noqa: BLE001 — map to safe envelope
                raise ApiError(
                    INTERNAL_ERROR,
                    "Query failed",
                    status_code=500,
                    details={"type": type(exc).__name__},
                ) from exc

        if req.force_agentic:
            chain.metadata = {**(chain.metadata or {}), "force_agentic": True}

        chain.cost.latency_ms = int((time.perf_counter() - t0) * 1000)
        chain.metadata = {
            **(chain.metadata or {}),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "query_id": chain.query_id,
        }

        if self.audit_store is not None:
            try:
                self.audit_store.save(chain)
            except Exception:
                pass

        if self.enable_cache and self.retrieval_cache is not None:
            try:
                self.retrieval_cache.set_answer(
                    req.question, chain.model_dump(mode="json")
                )
            except Exception:
                pass

        if self.multi_budget:
            self.multi_budget.commit(
                tenant_id=tenant_id,
                user_id=user_id,
                llm_calls=chain.cost.llm_calls,
                tokens=chain.cost.tokens,
                cost_units=max(0.01, chain.cost.llm_calls * 0.01),
            )

        get_metrics().record(
            QueryMetrics(
                query_id=chain.query_id,
                route=chain.route,
                hops=len(chain.steps),
                llm_calls=chain.cost.llm_calls,
                tokens=chain.cost.tokens,
                tool_calls=sum(len(s.tool_calls) for s in chain.steps),
                latency_ms=chain.cost.latency_ms,
                status=chain.status.value if chain.status else "",
                tenant_id=tenant_id,
                user_id=user_id,
            )
        )
        return _chain_to_data(chain)

    def stream_query_events(
        self,
        req: QueryRequest,
        *,
        tenant_id: str = "default",
        user_id: str = "anonymous",
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """Yield SSE (event, payload) pairs for progressive UI (P3-PERF-06)."""
        if self.enable_cache and self.retrieval_cache and not req.force_agentic:
            hit = self.retrieval_cache.get_answer(req.question)
            if hit is not None:
                yield EVENT_CACHE_HIT, {"query_id": hit.get("query_id")}
                yield EVENT_ANSWER, hit
                return

        # Pre-announce triage (best-effort, offline rules only for stream start)
        from agentic_graphrag.agent.triage import triage

        decision = triage(
            req.question,
            None,
            allow_llm=False,
            force_agentic=req.force_agentic,
            known_entities=self.known_entities,
        )
        yield EVENT_TRIAGE, decision.model_dump(mode="json")

        try:
            data = self.run_query(req, tenant_id=tenant_id, user_id=user_id)
            # Emit hop summaries from steps
            for step in data.steps:
                yield "sub_question", {
                    "hop": step.hop,
                    "sub_question": step.sub_question,
                }
                yield "hop_done", {
                    "hop": step.hop,
                    "conclusion": step.conclusion,
                    "critic_action": step.critic_action,
                }
            yield EVENT_ANSWER, data.model_dump(mode="json")
        except ApiError as exc:
            yield EVENT_ERROR, {"code": exc.code, "message": exc.message}
        except Exception as exc:  # noqa: BLE001
            yield EVENT_ERROR, {"code": INTERNAL_ERROR, "message": type(exc).__name__}

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
        # Attach to audit if present
        if self.audit_store is not None:
            chain = self.audit_store.get(query_id)
            if chain is not None:
                meta = dict(chain.get("metadata") or {})
                feedbacks = list(meta.get("feedback") or [])
                feedbacks.append(payload)
                meta["feedback"] = feedbacks
                chain["metadata"] = meta
                self.audit_store.save(chain)
        return payload

    def _build_executor(self) -> Executor:
        graph_ret = GraphRetriever.from_config(self.bundle.graph, self.cfg)
        fulltext_ret = FulltextRetriever(
            self.bundle.fulltext, top_k=self.cfg.retrieval.fulltext_top_k
        )
        llm_for_embed: LLMProvider | MockLLMProvider | None
        if self.allow_llm and self.settings.llm_api_key:
            llm_for_embed = build_llm_provider(settings=self.settings, cfg=self.cfg)
        else:
            llm_for_embed = MockLLMProvider()
        vector_ret = VectorRetriever(
            self.bundle.vector, llm_for_embed, top_k=self.cfg.retrieval.vector_top_k
        )
        return Executor(
            graph=graph_ret,
            vector=vector_ret,
            fulltext=fulltext_ret,
            llm=llm_for_embed if self.allow_llm else None,
            known_entities=self.known_entities,
            parallel=True,
            fusion_method="rrf",
            cache=self.retrieval_cache if self.enable_cache else None,
        )

    def _build_llm(self, budget: BudgetTracker) -> LLMProvider | MockLLMProvider:
        if self.allow_llm and self.settings.llm_api_key:
            return build_llm_provider(
                budget=budget,
                cache_dir=resolve_path(self.cfg.paths.cache_dir) / "llm",
                settings=self.settings,
                cfg=self.cfg,
            )
        return MockLLMProvider(budget=budget)


def _load_triples(path: Path) -> list[Triple]:
    if not path.exists():
        return []
    return [
        Triple.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _entities_from_triples(triples: list[Triple]) -> list[str]:
    names = {t.head.name.strip() for t in triples if t.head.name.strip()} | {
        t.tail.name.strip() for t in triples if t.tail.name.strip()
    }
    return sorted(names, key=lambda s: (-len(s), s.lower()))


def _chain_to_data(chain: ReasoningChain) -> QueryResultData:
    payload = chain.model_dump(mode="json")
    return QueryResultData.model_validate(payload)


def build_default_service() -> QueryService:
    """Factory used by FastAPI lifespan.

    Defaults to **offline** (memory graph + seed + MockLLM) so CI/local smoke
    stays deterministic. Opt into live LLM with ``AGR_ALLOW_LLM=1`` when a real
    ``LLM_API_KEY`` is configured (avoids 403/rate-limit flaking unit tests).
    """
    import os

    settings = get_settings()
    cfg = get_config()
    svc = QueryService.create_offline(cfg=cfg, settings=settings)
    allow = os.environ.get("AGR_ALLOW_LLM", "").lower() in {"1", "true", "yes"}
    if allow and settings.llm_api_key and "your-key" not in settings.llm_api_key:
        svc.allow_llm = True
    return svc
