"""Helpers for running eval cases (agr-run-cases)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.cli._common import _open_graph_store
from agentic_graphrag.cli.cases_retrievers import build_retrievers as build_retrievers  # noqa: F401
from agentic_graphrag.config import Settings
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.ports.stores import GraphStore
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph
from agentic_graphrag.knowledge.schema_check import Triple
from agentic_graphrag.observability.degradation import (
    REASON_CASE_FAILED,
    ChannelStatus,
    record_degradation,
)
from agentic_graphrag.stores.factory import GraphBackend


def load_seed_triples(seed_path: Path) -> list[Triple]:
    if not seed_path.exists():
        return []
    return [
        Triple.model_validate(json.loads(line))
        for line in seed_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def known_entity_names(triples: list[Triple]) -> list[str]:
    return sorted(
        {t.head.name.strip() for t in triples if t.head.name.strip()}
        | {t.tail.name.strip() for t in triples if t.tail.name.strip()},
        key=lambda s: (-len(s), s.lower()),
    )


def open_case_graph(
    settings: Settings,
    *,
    use_memory: bool,
    triples: list[Triple],
) -> tuple[GraphStore, GraphBackend]:
    graph_store, graph_backend = _open_graph_store(
        settings, memory=use_memory, allow_memory_fallback=False
    )
    if graph_backend is GraphBackend.MEMORY:
        if triples:
            load_triples_into_graph(graph_store, triples, clear_first=True)
        print(f"Loaded {len(triples)} seed triples into in-memory graph ({graph_store.counts()})")
    else:
        print(f"Using Neo4j graph store at {settings.neo4j_uri} ({graph_store.counts()})")
    return graph_store, graph_backend


def run_case_row(
    case: dict[str, Any],
    *,
    executor: Executor,
    llm: LLMClient | None,
    guard_cfg: GuardrailConfig,
    no_llm: bool,
    enable_triage: bool = False,
    force_agentic: bool = False,
) -> dict[str, Any]:
    """Run one case. Default stays full agentic; triage uses :func:`run_query`."""
    q = case["question"]
    budget = guard_cfg.budget_tracker()
    try:
        chain = _invoke_case(
            q,
            executor=executor,
            llm=None if no_llm else llm,
            guard_cfg=guard_cfg,
            budget=budget,
            no_llm=no_llm,
            enable_triage=enable_triage,
            force_agentic=force_agentic,
        )
        return _success_row(case, q, chain)
    except Exception as exc:
        record_degradation("eval", REASON_CASE_FAILED, exc, severity=ChannelStatus.FAILED)
        return _error_row(case, q, exc)


def _invoke_case(
    question: str,
    *,
    executor: Executor,
    llm: LLMClient | None,
    guard_cfg: GuardrailConfig,
    budget: BudgetTracker,
    no_llm: bool,
    enable_triage: bool,
    force_agentic: bool,
) -> ReasoningChain:
    if enable_triage or force_agentic:
        from agentic_graphrag.agent.loop import run_query

        return run_query(
            question,
            executor,
            llm,
            guard_cfg=guard_cfg,
            budget=budget,
            allow_llm=not no_llm,
            enable_triage=enable_triage and not force_agentic,
            force_agentic=force_agentic,
            known_entities=list(executor.known_entities or []),
        )
    from agentic_graphrag.agent.loop import run_agentic_query

    return run_agentic_query(
        question,
        executor,
        llm,
        guard_cfg=guard_cfg,
        budget=budget,
        allow_llm=not no_llm,
    )


def _success_row(case: dict[str, Any], q: str, chain: ReasoningChain) -> dict[str, Any]:
    cost = chain.cost.model_dump()
    meta = dict(chain.metadata or {})
    return {
        "case_id": case.get("id"),
        "question": q,
        "gold": case.get("gold_answer"),
        "gold_set": case.get("gold_set"),
        "prediction": chain.answer,
        "status": chain.status.value,
        "route": chain.route,
        "steps": len(chain.steps),
        "hop_count": max((s.hop for s in chain.steps), default=0),
        "latency_ms": cost.get("latency_ms", 0),
        "cost": cost,
        "graph_evidence": sum(
            1 for s in chain.steps for tc in s.tool_calls if tc.tool.startswith("graph") and tc.hits
        ),
        "explored_paths": chain.explored_paths[:20],
        "metadata": meta,
        "chain": chain.model_dump(),
    }


def _error_row(case: dict[str, Any], q: str, exc: Exception) -> dict[str, Any]:
    return {
        "case_id": case.get("id"),
        "question": q,
        "gold": case.get("gold_answer"),
        "prediction": "",
        "status": "error",
        "steps": 0,
        "hop_count": 0,
        "latency_ms": 0,
        "cost": {
            "llm_calls": 0,
            "tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "latency_ms": 0,
        },
        "error": str(exc),
    }


def build_eval_app(
    *, no_llm: bool, use_neo4j: bool, seed_triples: Path, enable_triage: bool
) -> QueryService:
    """QueryApp wired by the ``eval`` profile (or ``hybrid`` for ``--neo4j``).

    Refactor §8.1: evaluation runs the production path (budget, pipeline,
    audit, metrics) with answer/retrieval caches off.
    """
    from agentic_graphrag.bootstrap import (
        CAPABILITY_MATRIX,
        LLMMode,
        Profile,
        RuntimeProfile,
    )
    from agentic_graphrag.config import get_settings

    profile = Profile.HYBRID if use_neo4j else Profile.EVAL
    key = get_settings().llm_api_key
    live = (not no_llm) and bool(key) and "your-key" not in key
    rp = RuntimeProfile(
        profile=profile,
        caps=CAPABILITY_MATRIX[profile],
        llm_mode=LLMMode.LIVE if live else LLMMode.OFF,
    )
    app = QueryService.create_for_profile(rp, seed_triples=seed_triples)
    app.enable_cache = False
    if app.retrieval_cache is not None and not CAPABILITY_MATRIX[profile].retrieval_cache:
        app.retrieval_cache = None
    app.enable_triage = enable_triage
    return app


def run_case_row_via_app(
    case: dict[str, Any], *, app: QueryService, force_agentic: bool = False
) -> dict[str, Any]:
    from agentic_graphrag.app.contracts import QueryRequest

    q = case["question"]
    try:
        chain = app.run_query_chain(
            QueryRequest(question=q, force_agentic=force_agentic),
            # Evaluation owns an isolated store bundle and explicitly uses the
            # unscoped offline path; "eval" is not a shared production tenant.
            tenant_id="",
            user_id="eval",
        )
        return _success_row(case, q, chain)
    except Exception as exc:
        record_degradation("eval", REASON_CASE_FAILED, exc, severity=ChannelStatus.FAILED)
        return _error_row(case, q, exc)
