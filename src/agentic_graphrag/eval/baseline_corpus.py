"""Corpus loading helpers for baseline vector RAG."""

from __future__ import annotations

import json
from pathlib import Path

from agentic_graphrag.config import AppConfig, resolve_path
from agentic_graphrag.knowledge.ingest import chunk_document, load_documents_from_dir
from agentic_graphrag.llm.provider import LLMProvider, MockLLMProvider
from agentic_graphrag.stores.interfaces import ChunkRecord


def load_or_build_chunks(
    *,
    chunks_path: Path | None,
    raw_docs_dir: Path | None,
    cfg: AppConfig,
) -> list[ChunkRecord]:
    """Load chunks.jsonl, or chunk interim raw docs when missing (temp data path)."""
    if chunks_path and chunks_path.exists():
        return _load_chunks_jsonl(chunks_path)
    docs_dir = raw_docs_dir or resolve_path(cfg.paths.raw_docs_dir)
    if not docs_dir.exists():
        raise FileNotFoundError(f"No chunks at {chunks_path} and raw docs dir missing: {docs_dir}")
    chunks: list[ChunkRecord] = []
    for doc in load_documents_from_dir(docs_dir):
        chunks.extend(
            chunk_document(
                doc,
                chunk_size=cfg.knowledge.chunk_size_chars,
                overlap=cfg.knowledge.chunk_overlap_chars,
            )
        )
    return chunks


def _load_chunks_jsonl(chunks_path: Path) -> list[ChunkRecord]:
    out: list[ChunkRecord] = []
    for line in chunks_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        out.append(
            ChunkRecord(
                chunk_id=item["chunk_id"],
                doc_id=item["doc_id"],
                text=item["text"],
                index=int(item.get("index", 0)),
                metadata=item.get("metadata") or {},
                embedding=item.get("embedding"),
            )
        )
    return out


def ensure_embeddings(
    chunks: list[ChunkRecord],
    llm: LLMProvider | MockLLMProvider,
    *,
    embeddings_path: Path | None = None,
) -> list[ChunkRecord]:
    """Attach embeddings from cache file or compute via llm.embed."""
    by_id: dict[str, list[float]] = {}
    if embeddings_path and embeddings_path.exists():
        for line in embeddings_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get("embedding"):
                by_id[item["chunk_id"]] = list(item["embedding"])
    for ch in chunks:
        if ch.embedding:
            continue
        if ch.chunk_id in by_id:
            ch.embedding = by_id[ch.chunk_id]
        else:
            ch.embedding = llm.embed(ch.text)
    return chunks
