"""Vector + BM25 index command."""

from __future__ import annotations

import argparse
import json

from agentic_graphrag.config import get_config, get_settings, resolve_path
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.interfaces import ChunkRecord


def index_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build vector + BM25 indexes")
    parser.add_argument("--chunks", default=None)
    parser.add_argument(
        "--memory-vector", action="store_true", help="Use in-memory vector (no Qdrant)"
    )
    parser.add_argument("--no-embed", action="store_true", help="Skip embeddings (BM25 only)")
    args = parser.parse_args(argv)
    cfg = get_config()
    settings = get_settings()
    chunks_path = resolve_path(args.chunks or f"{cfg.paths.processed_dir}/chunks.jsonl")
    chunks = [
        ChunkRecord(**json.loads(line))
        for line in chunks_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    ft = BM25FulltextStore()
    n = ft.index(chunks)
    ft_path = resolve_path(f"{cfg.paths.indexes_dir}/bm25.json")
    ft.save(str(ft_path))
    print(f"BM25 indexed {n} chunks → {ft_path}")

    if args.no_embed:
        return

    from agentic_graphrag.llm.provider import LLMProvider
    from agentic_graphrag.stores.vector_store import InMemoryVectorStore, QdrantVectorStore

    llm = LLMProvider(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        embedding_model=cfg.llm.embedding_model,
        cache_dir=resolve_path(cfg.paths.cache_dir) / "llm",
    )
    for ch in chunks:
        ch.embedding = llm.embed(ch.text)

    if args.memory_vector:
        store = InMemoryVectorStore()
        store.ensure_collection(len(chunks[0].embedding) if chunks and chunks[0].embedding else 8)
        store.upsert(chunks)
        # Persist embeddings for offline reuse
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
    else:
        store = QdrantVectorStore(settings.qdrant_url, settings.qdrant_collection)
        if chunks and chunks[0].embedding:
            store.ensure_collection(len(chunks[0].embedding))
        n_vec = store.upsert(chunks)
        store.close()
        print(f"Qdrant upserted {n_vec} vectors")

