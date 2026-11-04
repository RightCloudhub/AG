"""Composition of a query service from configured stores and runtime sinks."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agentic_graphrag.app.wiring import (
    ANSWER_CACHE_TTL_SECONDS,
    _entities_from_triples,
    _load_seed_graph,
    _load_triples,
)
from agentic_graphrag.config import AppConfig, Settings, resolve_path, runtime_dir
from agentic_graphrag.governance.audit_store import AuditStore
from agentic_graphrag.governance.budget_policy import MultiLevelBudget
from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore
from agentic_graphrag.knowledge.review.queue import ReviewQueue
from agentic_graphrag.retrieval.cache import RetrievalCache
from agentic_graphrag.stores.factory import StoreBundle

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def service_from_bundle(
    service_type: type[QueryService],
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
    svc = service_type(
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
