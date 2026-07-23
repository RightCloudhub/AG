"""Document ingest command."""

from __future__ import annotations

import argparse
import json

from agentic_graphrag.cli._common import _ensure_dirs
from agentic_graphrag.config import get_config, resolve_path
from agentic_graphrag.knowledge.ingest import chunk_document, load_documents_from_dir
from agentic_graphrag.stores.doc_store import FileDocStore
from agentic_graphrag.stores.interfaces import ChunkRecord


def ingest_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest and chunk documents")
    parser.add_argument("--input", default=None, help="Raw docs directory")
    parser.add_argument("--out", default=None, help="Processed chunks JSONL path")
    args = parser.parse_args(argv)
    cfg = get_config()
    _ensure_dirs(cfg)
    input_dir = resolve_path(args.input or cfg.paths.raw_docs_dir)
    out_path = resolve_path(args.out or f"{cfg.paths.processed_dir}/chunks.jsonl")

    docs = load_documents_from_dir(input_dir)
    doc_store = FileDocStore(resolve_path(cfg.paths.processed_dir) / "docs")
    chunks: list[ChunkRecord] = []
    for doc in docs:
        doc_store.save(doc)
        chunks.extend(
            chunk_document(
                doc,
                chunk_size=cfg.knowledge.chunk_size_chars,
                overlap=cfg.knowledge.chunk_overlap_chars,
            )
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for ch in chunks:
            f.write(
                json.dumps(
                    {
                        "chunk_id": ch.chunk_id,
                        "doc_id": ch.doc_id,
                        "text": ch.text,
                        "index": ch.index,
                        "metadata": ch.metadata,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(f"Ingested {len(docs)} docs → {len(chunks)} chunks → {out_path}")
