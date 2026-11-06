"""Helpers for QueryService: triple loading, chain mapping, executor/LLM build."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentic_graphrag.agent.executor import Executor, ExecutorConfig, ExecutorDeps
from agentic_graphrag.app.contracts import QueryRequest, QueryResultData
from agentic_graphrag.app.degradation_policy import promote_degradation_fields
from agentic_graphrag.app.model_rerank import build_model_reranker
from agentic_graphrag.config import (
    AppConfig,
    GraphRetrievalConfig,
    RetrievalConfig,
    Settings,
    resolve_path,
)
from agentic_graphrag.core.enums import FusionMethod
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.ports.stores import GraphStore
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph
from agentic_graphrag.knowledge.schema_check import Triple
from agentic_graphrag.llm.factory import build_llm_provider
from agentic_graphrag.llm.provider import MockLLMProvider
from agentic_graphrag.observability.degradation import ChannelHealth
from agentic_graphrag.retrieval.cache import RetrievalCache
from agentic_graphrag.retrieval.fulltext import FulltextRetriever
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.retrieval.vector import VectorRetriever
from agentic_graphrag.stores.factory import StoreBundle

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService

ANSWER_CACHE_TTL_SECONDS = 3600.0
DEFAULT_FUSION_METHOD = FusionMethod.RRF
MIN_COST_UNITS = 0.01
COST_UNITS_PER_LLM_CALL = 0.01


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
    # Degradation state lives in chain metadata; promote it (same helper the
    # answer-cache replay path uses) so both report the same channels.
    payload = promote_degradation_fields(chain.model_dump(mode="json"))
    meta = payload.get("metadata") or {}
    # Evidence catalog promoted for UI citation click-through.
    if isinstance(meta, dict) and not payload.get("evidence"):
        payload["evidence"] = meta.get("evidence") or []
    payload["audit_status"] = meta.get("audit_status", "disabled")
    return QueryResultData.model_validate(payload)


def build_executor_for_service(
    *,
    bundle: StoreBundle,
    cfg: AppConfig,
    settings: Settings,
    allow_llm: bool,
    known_entities: list[str],
    retrieval_cache: RetrievalCache | None,
    enable_cache: bool,
    tenant_id: str | None = None,
    kb_id: str = "default",
    budget: BudgetTracker | None = None,
    model_llm: LLMClient | None = None,
) -> Executor:
    ret = cfg.retrieval
    # One health object records all degraded channels in this query.
    health = ChannelHealth()
    llm_for_embed = model_llm or _embed_llm(
        allow_llm=allow_llm,
        settings=settings,
        cfg=cfg,
        tenant_id=tenant_id or "default",
        kb_id=kb_id,
        budget=budget,
    )
    reranker = build_model_reranker(ret.rerank, llm_for_embed, health=health) if allow_llm else None
    vec_k, ft_k = _recall_sizes(ret, reranked=reranker is not None)
    graph_cfg = _graph_recall_config(ret, reranked=reranker is not None)
    graph_ret = GraphRetriever.from_config(bundle.graph, graph_cfg, health=health)
    fulltext_ret = FulltextRetriever(bundle.fulltext, top_k=ft_k, health=health)
    vector_ret = VectorRetriever(bundle.vector, llm_for_embed, top_k=vec_k, health=health)
    deps = ExecutorDeps(
        vector=vector_ret,
        fulltext=fulltext_ret,
        llm=llm_for_embed if allow_llm else None,
        known_entities=known_entities,
        tenant_id=tenant_id,
        health=health,
    )
    config = ExecutorConfig(
        parallel=bool(ret.parallel),
        fusion_method=(ret.fusion_method or DEFAULT_FUSION_METHOD),
        fusion_k=int(ret.fusion_k),
        cache=retrieval_cache if enable_cache and reranker is None else None,
        reranker=reranker,
        candidate_pool=ret.rerank.candidate_pool if reranker is not None else None,
    )
    if reranker is not None:
        config.fusion_limit = ret.rerank.top_n or ret.top_k
    return Executor(graph=graph_ret, deps=deps, config=config)


def _recall_sizes(ret: RetrievalConfig, *, reranked: bool) -> tuple[int, int]:
    """Stage-1 recall per channel: widened to the candidate pool when re-ranking."""
    if not reranked:
        return int(ret.vector_top_k), int(ret.fulltext_top_k)
    pool = int(ret.rerank.candidate_pool)
    return pool, pool


def _graph_recall_config(ret: RetrievalConfig, *, reranked: bool) -> GraphRetrievalConfig:
    graph = ret.graph.model_copy(deep=True)
    if reranked:
        pool = ret.rerank.candidate_pool
        graph.max_neighbors_per_layer = max(pool, graph.max_neighbors_per_layer)
        graph.max_paths = max(pool, graph.max_paths)
        graph.beam_width = max(pool, graph.beam_width)
    return graph


def build_llm_for_service(
    *,
    allow_llm: bool,
    settings: Settings,
    cfg: AppConfig,
    budget: BudgetTracker,
    tenant_id: str = "default",
    kb_id: str = "default",
) -> LLMClient | MockLLMProvider:
    if allow_llm:
        return build_llm_provider(
            budget=budget,
            cache_dir=resolve_path(cfg.paths.cache_dir) / "llm",
            settings=settings,
            cfg=cfg,
            tenant_id=tenant_id,
            kb_id=kb_id,
        )
    return MockLLMProvider(budget=budget)


def _embed_llm(
    *,
    allow_llm: bool,
    settings: Settings,
    cfg: AppConfig,
    tenant_id: str = "default",
    kb_id: str = "default",
    budget: BudgetTracker | None = None,
) -> LLMClient | MockLLMProvider:
    if allow_llm:
        return build_llm_provider(
            settings=settings, cfg=cfg, budget=budget, tenant_id=tenant_id, kb_id=kb_id
        )
    return MockLLMProvider()


def cost_units_for_chain(llm_calls: int) -> float:
    return max(MIN_COST_UNITS, llm_calls * COST_UNITS_PER_LLM_CALL)


def stream_cache_hit_events(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str = "default",
    user_id: str = "anonymous",
) -> list[tuple[str, dict[str, Any]]] | None:
    """Return SSE events for a cached answer, or None on miss."""
    from agentic_graphrag.app.answer_cache import cached_chain
    from agentic_graphrag.app.stream_events import EVENT_ANSWER, EVENT_CACHE_HIT

    chain = cached_chain(svc, req, tenant_id=tenant_id, user_id=user_id)
    if chain is None:
        return None
    return [
        (EVENT_CACHE_HIT, {"query_id": chain.query_id}),
        (EVENT_ANSWER, chain.model_dump(mode="json")),
    ]


def _load_seed_graph(graph: GraphStore, triples: list[Triple]) -> None:
    """Load seed triples through the ingestion gate, logging any rejections."""
    stats = load_triples_into_graph(graph, triples, clear_first=True)
    rejected = int(stats.get("triples_rejected", 0))
    if rejected:
        logging.getLogger(__name__).warning(
            "seed graph: %s/%s triples rejected by the ingestion gate (%s)",
            rejected,
            stats.get("triples_input", len(triples)),
            stats.get("rejection_reasons", {}),
        )
