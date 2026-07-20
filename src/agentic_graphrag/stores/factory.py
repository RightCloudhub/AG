"""Store factory / composition root (P2-ARCH-02).

Centralizes backend selection so CLI and API share the same wiring.
Application code depends on ``GraphStore`` / ``VectorStore`` / … protocols,
not on Neo4j or Qdrant client types.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from agentic_graphrag.config import AppConfig, Settings, get_config, get_settings, resolve_path
from agentic_graphrag.stores.doc_store import FileDocStore, InMemoryDocStore
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.interfaces import DocStore, FulltextStore, GraphStore, VectorStore
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore


class GraphBackend(StrEnum):
    MEMORY = "memory"
    NEO4J = "neo4j"


class VectorBackend(StrEnum):
    MEMORY = "memory"
    QDRANT = "qdrant"


@dataclass
class StoreBundle:
    """Process-local set of repository implementations."""

    graph: GraphStore
    vector: VectorStore
    fulltext: FulltextStore
    docs: DocStore
    graph_backend: GraphBackend
    vector_backend: VectorBackend

    def close(self) -> None:
        close = getattr(self.graph, "close", None)
        if callable(close):
            close()
        close_v = getattr(self.vector, "close", None)
        if callable(close_v):
            close_v()


def create_graph_store(
    backend: GraphBackend | Literal["memory", "neo4j"] = GraphBackend.MEMORY,
    *,
    settings: Settings | None = None,
    ping: bool = True,
) -> tuple[GraphStore, GraphBackend]:
    """Construct a GraphStore. Raises on Neo4j connectivity failure when requested."""
    backend = GraphBackend(backend)
    settings = settings or get_settings()
    if backend is GraphBackend.MEMORY:
        return InMemoryGraphStore(), GraphBackend.MEMORY

    from agentic_graphrag.stores.neo4j_store import Neo4jGraphStore

    store = Neo4jGraphStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    if ping:
        store.ping()
    return store, GraphBackend.NEO4J


def create_vector_store(
    backend: VectorBackend | Literal["memory", "qdrant"] = VectorBackend.MEMORY,
    *,
    settings: Settings | None = None,
) -> tuple[VectorStore, VectorBackend]:
    backend = VectorBackend(backend)
    settings = settings or get_settings()
    if backend is VectorBackend.MEMORY:
        return InMemoryVectorStore(), VectorBackend.MEMORY

    from agentic_graphrag.stores.vector_store import QdrantVectorStore

    return (
        QdrantVectorStore(settings.qdrant_url, settings.qdrant_collection),
        VectorBackend.QDRANT,
    )


def create_fulltext_store() -> FulltextStore:
    return BM25FulltextStore()


def create_doc_store(
    *,
    root: str | Path | None = None,
    memory: bool = False,
    cfg: AppConfig | None = None,
) -> DocStore:
    if memory:
        return InMemoryDocStore()
    cfg = cfg or get_config()
    path = resolve_path(root or Path(cfg.paths.processed_dir) / "docs")
    return FileDocStore(path)


def create_offline_bundle(
    *,
    cfg: AppConfig | None = None,
    settings: Settings | None = None,
    load_bm25: bool = True,
    load_embeddings: bool = True,
) -> StoreBundle:
    """Default offline bundle: memory graph + memory vectors + BM25 + file docs."""
    cfg = cfg or get_config()
    settings = settings or get_settings()
    graph, g_backend = create_graph_store(GraphBackend.MEMORY, settings=settings)
    vector, v_backend = create_vector_store(VectorBackend.MEMORY, settings=settings)
    fulltext = create_fulltext_store()
    docs = create_doc_store(cfg=cfg)
    if load_bm25:
        _try_load_bm25(fulltext, cfg)
    if load_embeddings:
        _try_load_embeddings(vector, cfg)
    return StoreBundle(
        graph=graph,
        vector=vector,
        fulltext=fulltext,
        docs=docs,
        graph_backend=g_backend,
        vector_backend=v_backend,
    )


def _try_load_bm25(fulltext: FulltextStore, cfg: AppConfig) -> None:
    ft_path = resolve_path(f"{cfg.paths.indexes_dir}/bm25.json")
    load = getattr(fulltext, "load", None)
    if callable(load) and ft_path.exists():
        load(str(ft_path))


def _try_load_embeddings(vector: VectorStore, cfg: AppConfig) -> None:
    emb_path = resolve_path(f"{cfg.paths.indexes_dir}/embeddings.jsonl")
    if not emb_path.exists():
        return
    import json

    from agentic_graphrag.stores.interfaces import ChunkRecord

    chunks: list[Any] = []
    for line in emb_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        chunks.append(
            ChunkRecord(
                chunk_id=item["chunk_id"],
                doc_id=item["doc_id"],
                text=item["text"],
                index=item.get("index", 0),
                embedding=item.get("embedding"),
            )
        )
    if chunks:
        vector.upsert(chunks)


def create_live_bundle(
    *,
    cfg: AppConfig | None = None,
    settings: Settings | None = None,
    allow_memory_graph_fallback: bool = False,
) -> StoreBundle:
    """Live backends: Neo4j + Qdrant (+ BM25). Optional memory graph fallback."""
    cfg = cfg or get_config()
    settings = settings or get_settings()
    try:
        graph, g_backend = create_graph_store(GraphBackend.NEO4J, settings=settings, ping=True)
    except Exception:
        if not allow_memory_graph_fallback:
            raise
        graph, g_backend = create_graph_store(GraphBackend.MEMORY, settings=settings)

    try:
        vector, v_backend = create_vector_store(VectorBackend.QDRANT, settings=settings)
    except Exception:
        vector, v_backend = create_vector_store(VectorBackend.MEMORY, settings=settings)

    fulltext = create_fulltext_store()
    ft_path = resolve_path(f"{cfg.paths.indexes_dir}/bm25.json")
    load = getattr(fulltext, "load", None)
    if callable(load) and ft_path.exists():
        load(str(ft_path))

    docs = create_doc_store(cfg=cfg)
    return StoreBundle(
        graph=graph,
        vector=vector,
        fulltext=fulltext,
        docs=docs,
        graph_backend=g_backend,
        vector_backend=v_backend,
    )
