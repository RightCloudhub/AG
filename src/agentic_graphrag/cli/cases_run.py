"""Helpers for running eval cases (agr-run-cases)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from agentic_graphrag.cli._common import _open_graph_store
from agentic_graphrag.config import AppConfig, Settings, resolve_path
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph
from agentic_graphrag.knowledge.schema_check import Triple
from agentic_graphrag.llm.provider import LLMProvider, MockLLMProvider
from agentic_graphrag.retrieval.fulltext import FulltextRetriever
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.retrieval.vector import VectorRetriever
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.interfaces import ChunkRecord
from agentic_graphrag.stores.vector_store import InMemoryVectorStore, QdrantVectorStore


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
) -> tuple[Any, str]:
    graph_store, graph_backend = _open_graph_store(
        settings, memory=use_memory, allow_memory_fallback=False
    )
    if graph_backend == "memory":
        if triples:
            load_triples_into_graph(graph_store, triples, clear_first=True)
        print(f"Loaded {len(triples)} seed triples into in-memory graph ({graph_store.counts()})")
    else:
        print(f"Using Neo4j graph store at {settings.neo4j_uri} ({graph_store.counts()})")
    return graph_store, graph_backend


def build_retrievers(
    cfg: AppConfig,
    settings: Settings,
    graph_store: Any,
    *,
    no_llm: bool,
) -> tuple[
    GraphRetriever,
    FulltextRetriever,
    VectorRetriever | None,
    LLMProvider | MockLLMProvider,
]:
    ft_store = BM25FulltextStore()
    ft_path = resolve_path(f"{cfg.paths.indexes_dir}/bm25.json")
    if ft_path.exists():
        ft_store.load(str(ft_path))
    fulltext_ret = FulltextRetriever(ft_store, top_k=cfg.retrieval.fulltext_top_k)
    vector_ret, llm = _vector_and_llm(cfg, settings, no_llm=no_llm)
    rel_scorer = None
    if not no_llm:
        from agentic_graphrag.retrieval.relation_embed import make_relation_embed_sim

        rel_scorer = make_relation_embed_sim(llm)
    graph_ret = GraphRetriever.from_config(
        graph_store, cfg, relation_embed_sim=rel_scorer
    )
    return graph_ret, fulltext_ret, vector_ret, llm


def _vector_and_llm(
    cfg: AppConfig, settings: Settings, *, no_llm: bool
) -> tuple[VectorRetriever | None, LLMProvider | MockLLMProvider]:
    if no_llm:
        return _offline_vector(cfg), MockLLMProvider()
    from agentic_graphrag.config import build_llm_provider

    llm = build_llm_provider(
        cache_dir=resolve_path(cfg.paths.cache_dir) / "llm",
        settings=settings,
        cfg=cfg,
    )
    try:
        vstore = QdrantVectorStore(settings.qdrant_url, settings.qdrant_collection)
        return VectorRetriever(vstore, llm, top_k=cfg.retrieval.vector_top_k), llm
    except Exception as exc:
        print(f"Warning: vector store unavailable: {exc}", file=sys.stderr)
        return None, llm


def _offline_vector(cfg: AppConfig) -> VectorRetriever | None:
    emb_path = resolve_path(f"{cfg.paths.indexes_dir}/embeddings.jsonl")
    if not emb_path.exists():
        return None
    llm = MockLLMProvider()
    vstore = InMemoryVectorStore()
    chunks = []
    for line in emb_path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        chunks.append(
            ChunkRecord(
                chunk_id=item["chunk_id"],
                doc_id=item["doc_id"],
                text=item["text"],
                index=item.get("index", 0),
                embedding=item["embedding"],
            )
        )
    if not chunks:
        return None
    vstore.upsert(chunks)
    return VectorRetriever(vstore, llm, top_k=cfg.retrieval.vector_top_k)


def run_case_row(
    case: dict[str, Any],
    *,
    executor: Any,
    llm: Any,
    guard_cfg: Any,
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
        return _error_row(case, q, exc)


def _invoke_case(
    question: str,
    *,
    executor: Any,
    llm: Any,
    guard_cfg: Any,
    budget: Any,
    no_llm: bool,
    enable_triage: bool,
    force_agentic: bool,
) -> Any:
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


def _success_row(case: dict[str, Any], q: str, chain: Any) -> dict[str, Any]:
    cost = chain.cost.model_dump()
    meta = dict(chain.metadata or {})
    return {
        "case_id": case.get("id"),
        "question": q,
        "gold": case.get("gold_answer"),
        "prediction": chain.answer,
        "status": chain.status.value,
        "route": chain.route,
        "steps": len(chain.steps),
        "hop_count": max((s.hop for s in chain.steps), default=0),
        "latency_ms": cost.get("latency_ms", 0),
        "cost": cost,
        "graph_evidence": sum(
            1
            for s in chain.steps
            for tc in s.tool_calls
            if tc.tool.startswith("graph") and tc.hits
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
