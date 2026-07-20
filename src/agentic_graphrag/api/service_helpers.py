"""Helpers for QueryService: triple loading, chain mapping, executor/LLM build."""

from __future__ import annotations

import json
from pathlib import Path

from agentic_graphrag.agent.executor import Executor, ExecutorConfig, ExecutorDeps
from agentic_graphrag.api.schemas import QueryResultData
from agentic_graphrag.config import (
    AppConfig,
    Settings,
    build_llm_provider,
    resolve_path,
)
from agentic_graphrag.generation.trace import ReasoningChain
from agentic_graphrag.knowledge.schema_check import Triple
from agentic_graphrag.llm.budget import BudgetTracker
from agentic_graphrag.llm.provider import LLMProvider, MockLLMProvider
from agentic_graphrag.retrieval.fulltext import FulltextRetriever
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.retrieval.vector import VectorRetriever
from agentic_graphrag.stores.factory import StoreBundle

ANSWER_CACHE_TTL_SECONDS = 3600.0
DEFAULT_FUSION_METHOD = "rrf"
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
    payload = chain.model_dump(mode="json")
    return QueryResultData.model_validate(payload)


def build_executor_for_service(
    *,
    bundle: StoreBundle,
    cfg: AppConfig,
    settings: Settings,
    allow_llm: bool,
    known_entities: list[str],
    retrieval_cache: object | None,
    enable_cache: bool,
) -> Executor:
    graph_ret = GraphRetriever.from_config(bundle.graph, cfg)
    fulltext_ret = FulltextRetriever(bundle.fulltext, top_k=cfg.retrieval.fulltext_top_k)
    llm_for_embed = _embed_llm(allow_llm=allow_llm, settings=settings, cfg=cfg)
    vector_ret = VectorRetriever(
        bundle.vector, llm_for_embed, top_k=cfg.retrieval.vector_top_k
    )
    deps = ExecutorDeps(
        vector=vector_ret,
        fulltext=fulltext_ret,
        llm=llm_for_embed if allow_llm else None,
        known_entities=known_entities,
    )
    config = ExecutorConfig(
        parallel=True,
        fusion_method=DEFAULT_FUSION_METHOD,
        cache=retrieval_cache if enable_cache else None,  # type: ignore[arg-type]
    )
    return Executor(graph=graph_ret, deps=deps, config=config)


def build_llm_for_service(
    *,
    allow_llm: bool,
    settings: Settings,
    cfg: AppConfig,
    budget: BudgetTracker,
) -> LLMProvider | MockLLMProvider:
    if allow_llm and settings.llm_api_key:
        return build_llm_provider(
            budget=budget,
            cache_dir=resolve_path(cfg.paths.cache_dir) / "llm",
            settings=settings,
            cfg=cfg,
        )
    return MockLLMProvider(budget=budget)


def _embed_llm(
    *,
    allow_llm: bool,
    settings: Settings,
    cfg: AppConfig,
) -> LLMProvider | MockLLMProvider:
    if allow_llm and settings.llm_api_key:
        return build_llm_provider(settings=settings, cfg=cfg)
    return MockLLMProvider()


def cost_units_for_chain(llm_calls: int) -> float:
    return max(MIN_COST_UNITS, llm_calls * COST_UNITS_PER_LLM_CALL)
