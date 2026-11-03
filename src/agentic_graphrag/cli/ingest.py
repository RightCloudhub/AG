"""Document ingest command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_graphrag.cli._common import _ensure_dirs
from agentic_graphrag.config import get_config, resolve_path, runtime_dir
from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.knowledge.ingest import chunk_document, load_documents_from_dir
from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore
from agentic_graphrag.knowledge.ingest_worker_cli import build_cli_worker
from agentic_graphrag.knowledge.parsers import create_parser


def ingest_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest and chunk documents")
    parser.add_argument("--input", default=None, help="Raw docs directory")
    parser.add_argument("--out", default=None, help="Processed chunks JSONL path")
    parser.add_argument("--tenant", default="default")
    args = parser.parse_args(argv)
    cfg = get_config()
    _ensure_dirs(cfg)
    input_dir = resolve_path(args.input or cfg.paths.raw_docs_dir)
    out_path = resolve_path(args.out or f"{cfg.paths.processed_dir}/chunks.jsonl")

    docs = load_documents_from_dir(input_dir, parser=create_parser(cfg.knowledge.parsing))
    worker = build_cli_worker()
    doc_store = worker.doc_store
    tasks = IngestTaskStore(runtime_dir(cfg) / "ingest_tasks.jsonl")
    chunks: list[ChunkRecord] = []
    for doc in docs:
        doc.tenant_id = args.tenant
        doc.source_uri = Path(doc.metadata["source_path"]).resolve().as_uri()
        row = worker.pipeline.lifecycle.accept(doc)
        if row["status"] != "duplicate":
            tasks.create(args.tenant, [row])
        stored = doc_store.get(row["doc_id"], tenant_id=args.tenant)
        assert stored is not None
        doc = stored
        chunks.extend(
            chunk_document(
                doc,
                chunk_size=cfg.knowledge.chunk_size_tokens,
                overlap=cfg.knowledge.chunk_overlap_tokens,
                tokenizer=cfg.knowledge.tokenizer,
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
                        "tenant_id": ch.tenant_id,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(f"Ingested {len(docs)} docs → {len(chunks)} chunks → {out_path}")
