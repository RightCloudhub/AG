"""Query application service — wires stores + agent loop for the API."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import run_agentic_query
from agentic_graphrag.api.errors import BUDGET_EXCEEDED, INTERNAL_ERROR, ApiError
from agentic_graphrag.api.schemas import QueryRequest, QueryResultData
from agentic_graphrag.config import AppConfig, Settings, get_config, get_settings, resolve_path
from agentic_graphrag.generation.trace import ReasoningChain
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph
from agentic_graphrag.knowledge.schema_check import Triple
from agentic_graphrag.llm.budget import BudgetExceeded, BudgetTracker
from agentic_graphrag.llm.provider import LLMProvider, MockLLMProvider
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
        return cls(
            cfg=cfg,
            settings=settings,
            bundle=bundle,
            allow_llm=False,
            known_entities=known,
        )

    def close(self) -> None:
        self.bundle.close()

    def run_query(self, req: QueryRequest) -> QueryResultData:
        timeout_override = int(req.timeout_ms / 1000) if req.timeout_ms is not None else None
        guard_cfg = GuardrailConfig.from_app_config(
            self.cfg,
            max_hops=req.max_hops,
            query_timeout_seconds=timeout_override,
        )
        budget = guard_cfg.budget_tracker()

        with self._lock:
            executor = self._build_executor()
            llm = self._build_llm(budget)
            try:
                chain = run_agentic_query(
                    req.question,
                    executor,
                    llm if self.allow_llm else None,
                    guard_cfg=guard_cfg,
                    budget=budget,
                    allow_llm=self.allow_llm,
                )
            except BudgetExceeded as exc:
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

        # force_agentic is accepted for schema completeness (no triage yet in MVP)
        if req.force_agentic:
            chain.metadata = {**(chain.metadata or {}), "force_agentic": True}

        return _chain_to_data(chain)

    def _build_executor(self) -> Executor:
        graph_ret = GraphRetriever.from_config(self.bundle.graph, self.cfg)
        fulltext_ret = FulltextRetriever(
            self.bundle.fulltext, top_k=self.cfg.retrieval.fulltext_top_k
        )
        llm_for_embed: LLMProvider | MockLLMProvider | None
        if self.allow_llm and self.settings.llm_api_key:
            llm_for_embed = LLMProvider(
                api_key=self.settings.llm_api_key,
                base_url=self.settings.llm_base_url,
                strong_model=self.cfg.llm.strong_model,
                light_model=self.cfg.llm.light_model,
                embedding_model=self.cfg.llm.embedding_model,
            )
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
        )

    def _build_llm(self, budget: BudgetTracker) -> LLMProvider | MockLLMProvider:
        if self.allow_llm and self.settings.llm_api_key:
            return LLMProvider(
                api_key=self.settings.llm_api_key,
                base_url=self.settings.llm_base_url,
                strong_model=self.cfg.llm.strong_model,
                light_model=self.cfg.llm.light_model,
                embedding_model=self.cfg.llm.embedding_model,
                budget=budget,
                cache_dir=resolve_path(self.cfg.paths.cache_dir) / "llm",
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
