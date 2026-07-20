"""Vector + BM25 index command."""

from __future__ import annotations

import argparse
import json

from agentic_graphrag.config import get_config, get_settings, resolve_path
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.interfaces import ChunkRecord


def index_main(argv: list[str] | None = None) -> None:
    args = _parse_index(argv)
    cfg = get_config()
    settings = get_settings()
    chunks_path = resolve_path(args.chunks or f"{cfg.paths.processed_dir}/chunks.jsonl")
    chunks = [
        ChunkRecord(**json.loads(line))
        for line in chunks_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    _index_bm25(chunks, cfg)
    if args.no_embed:
        return
    _index_vectors(chunks, cfg, settings, memory=args.memory_vector)


def _parse_index(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build vector + BM25 indexes")
    parser.add_argument("--chunks", default=None)
    parser.add_argument("--memory-vector", action="store_true")
    parser.add_argument("--no-embed", action="store_true")
    return parser.parse_args(argv)


def _index_bm25(chunks: list[ChunkRecord], cfg) -> None:
    ft = BM25FulltextStore()
    n = ft.index(chunks)
    ft_path = resolve_path(f"{cfg.paths.indexes_dir}/bm25.json")
    ft.save(str(ft_path))
    print(f"BM25 indexed {n} chunks → {ft_path}")


def _index_vectors(chunks: list[ChunkRecord], cfg, settings, *, memory: bool) -> None:
    from agentic_graphrag.config import build_llm_provider
    from agentic_graphrag.stores.vector_store import QdrantVectorStore

    llm = build_llm_provider(
        cache_dir=resolve_path(cfg.paths.cache_dir) / "llm",
        settings=settings,
        cfg=cfg,
    )
    for ch in chunks:
        ch.embedding = llm.embed(ch.text)
    if memory:
        _persist_memory_vectors(chunks, cfg)
        return
    store = QdrantVectorStore(settings.qdrant_url, settings.qdrant_collection)
    if chunks and chunks[0].embedding:
        store.ensure_collection(len(chunks[0].embedding))
    n_vec = store.upsert(chunks)
    store.close()
    print(f"Qdrant upserted {n_vec} vectors")


def _persist_memory_vectors(chunks: list[ChunkRecord], cfg) -> None:
    from agentic_graphrag.stores.vector_store import InMemoryVectorStore

    store = InMemoryVectorStore()
    dim = len(chunks[0].embedding) if chunks and chunks[0].embedding else 8
    store.ensure_collection(dim)
    store.upsert(chunks)
    emb_path = resolve_path(f"{cfg.paths.indexes_dir}/embeddings.jsonl")
    with emb_path.open("w", encoding="utf-8") as f:
        for ch in chunks:
            f.write(
                json.dumps(
                    {
                        "chunk_id": ch.chunk_id,
                        "embedding": ch.embedding,
                        "text": ch.text,
                        "doc_id": ch.doc_id,
                        "index": ch.index,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(f"In-memory vectors prepared → {emb_path}")
