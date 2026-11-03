"""Retriever construction for eval runs (agr-run-cases).

Split out of :mod:`agentic_graphrag.cli.cases_run` (300-line gate): the case
runners stay there, building the graph/fulltext/vector retrievers lives here.
"""

from __future__ import annotations

import json
import sys

from agentic_graphrag.config import AppConfig, Settings, resolve_path
from agentic_graphrag.core.enums import RetrievalChannel
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.ports.stores import ChunkRecord, GraphStore
from agentic_graphrag.llm.provider import MockLLMProvider
from agentic_graphrag.observability.degradation import (
    REASON_VECTOR_STORE_UNAVAILABLE,
    ChannelStatus,
    record_degradation,
)
from agentic_graphrag.retrieval.fulltext import FulltextRetriever
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.retrieval.vector import VectorRetriever
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore, QdrantVectorStore


def build_retrievers(
    cfg: AppConfig,
    settings: Settings,
    graph_store: GraphStore,
    *,
    no_llm: bool,
) -> tuple[
    GraphRetriever,
    FulltextRetriever,
    VectorRetriever | None,
    LLMClient | MockLLMProvider,
]:
    graph_ret = GraphRetriever.from_config(graph_store, cfg)
    ft_store = BM25FulltextStore()
    ft_path = resolve_path(f"{cfg.paths.indexes_dir}/bm25.json")
    if ft_path.exists():
        ft_store.load(str(ft_path))
    fulltext_ret = FulltextRetriever(ft_store, top_k=cfg.retrieval.fulltext_top_k)
    vector_ret, llm = _vector_and_llm(cfg, settings, no_llm=no_llm)
    return graph_ret, fulltext_ret, vector_ret, llm


def _vector_and_llm(
    cfg: AppConfig, settings: Settings, *, no_llm: bool
) -> tuple[VectorRetriever | None, LLMClient | MockLLMProvider]:
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
        record_degradation(
            RetrievalChannel.VECTOR,
            REASON_VECTOR_STORE_UNAVAILABLE,
            exc,
            severity=ChannelStatus.FAILED,
        )
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
                tenant_id=str(
                    item.get("tenant_id") or (item.get("metadata") or {}).get("tenant_id") or ""
                ),
                embedding_model=str(item.get("embedding_model") or ""),
                embedding=item["embedding"],
            )
        )
    if not chunks:
        return None
    vstore.upsert(chunks)
    return VectorRetriever(vstore, llm, top_k=cfg.retrieval.vector_top_k)
