"""Build-graph command (extract + load)."""

from __future__ import annotations

import argparse
import json
import sys

from agentic_graphrag.cli._common import _open_graph_store
from agentic_graphrag.config import get_config, get_settings, resolve_path
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph
from agentic_graphrag.knowledge.schema_check import Triple, gate_triples, load_schema
from agentic_graphrag.stores.interfaces import ChunkRecord


def build_graph_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Extract triples and load graph (Neo4j by default; --memory-graph for offline)"
    )
    parser.add_argument("--chunks", default=None, help="chunks.jsonl path")
    parser.add_argument("--triples", default=None, help="Optional precomputed triples JSONL")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM; load --triples only")
    parser.add_argument(
        "--memory-graph",
        action="store_true",
        help="Use in-memory graph (no Neo4j; process-local dry-run / offline seed validation)",
    )
    parser.add_argument("--no-clear", action="store_true", help="Do not clear graph first")
    args = parser.parse_args(argv)
    cfg = get_config()
    settings = get_settings()
    schema = load_schema(resolve_path(cfg.knowledge.schema_path))

    triples: list[Triple] = []
    if args.triples:
        for line in resolve_path(args.triples).read_text(encoding="utf-8").splitlines():
            if line.strip():
                triples.append(Triple.model_validate(json.loads(line)))
    elif not args.no_llm:
        from agentic_graphrag.knowledge.extraction import RetryPolicy, run_extract_pipeline
        from agentic_graphrag.llm.budget import BudgetTracker
        from agentic_graphrag.llm.provider import LLMProvider
        from agentic_graphrag.stores.doc_store import FileDocStore

        chunks_path = resolve_path(args.chunks or f"{cfg.paths.processed_dir}/chunks.jsonl")
        chunks = [
            ChunkRecord(**json.loads(line))
            for line in chunks_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        budget = BudgetTracker(max_llm_calls=10_000, max_tokens=10_000_000)
        llm = LLMProvider(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            strong_model=cfg.llm.strong_model,
            light_model=cfg.llm.light_model,
            embedding_model=cfg.llm.embedding_model,
            budget=budget,
            cache_dir=resolve_path(cfg.paths.cache_dir) / "llm",
        )
        doc_store = FileDocStore(resolve_path(cfg.paths.processed_dir) / "docs")
        pipe = run_extract_pipeline(
            chunks,
            schema,
            llm=llm,
            confidence_threshold=cfg.knowledge.extract_confidence_threshold,
            retry=RetryPolicy(
                max_attempts=cfg.knowledge.extract_max_attempts,
                base_delay_seconds=cfg.knowledge.extract_retry_base_delay_seconds,
            ),
            journal_path=resolve_path(cfg.knowledge.extract_journal_path),
            quarantine_path=resolve_path(cfg.knowledge.extract_quarantine_path),
            doc_store=doc_store,
        )
        triples = pipe.accepted
        reject_path = resolve_path(f"{cfg.paths.processed_dir}/rejected_triples.jsonl")
        with reject_path.open("w", encoding="utf-8") as f:
            for t, reason in pipe.rejected:
                f.write(
                    json.dumps({"triple": t.model_dump(), "reason": reason}, ensure_ascii=False)
                    + "\n"
                )
        print(
            f"Extracted accepted={len(pipe.accepted)} rejected={len(pipe.rejected)} "
            f"ok_chunks={pipe.ok_count} failed={pipe.failed_count} skipped={pipe.skipped_count}"
        )
        triples_path = resolve_path(f"{cfg.paths.processed_dir}/triples.jsonl")
        with triples_path.open("w", encoding="utf-8") as f:
            for t in triples:
                f.write(json.dumps(t.model_dump(), ensure_ascii=False) + "\n")
    else:
        print("--no-llm requires --triples", file=sys.stderr)
        sys.exit(2)

    # P2-KG-02/03: schema + confidence gate before any write
    thr = cfg.knowledge.extract_confidence_threshold
    gated = gate_triples(triples, schema, confidence_threshold=thr)
    reject_path = resolve_path(f"{cfg.paths.processed_dir}/rejected_triples.jsonl")
    with reject_path.open("w", encoding="utf-8") as f:
        for t, reason in gated.rejected:
            f.write(
                json.dumps({"triple": t.model_dump(), "reason": reason}, ensure_ascii=False) + "\n"
            )
    triples = gated.accepted
    print(
        f"Ingestion gate: accepted={len(triples)} rejected={len(gated.rejected)} "
        f"(threshold={thr}) → {reject_path}",
        flush=True,
    )
    if gated.rejection_reasons:
        print(f"Rejection reasons: {gated.rejection_reasons}", flush=True)

    # Seed / --no-llm path is offline-friendly: prefer Neo4j when up, else memory.
    # LLM extract path requires Neo4j (no silent fallback).
    store, backend = _open_graph_store(
        settings,
        memory=args.memory_graph,
        allow_memory_fallback=args.no_llm,
    )
    try:
        stats = load_triples_into_graph(
            store,
            triples,
            clear_first=not args.no_clear,
            # Already gated above; pass schema=None to avoid double-filter
        )
        stats["backend"] = backend
        stats["gate_rejected"] = len(gated.rejected)
        print(json.dumps(stats, indent=2), flush=True)
        if backend == "memory":
            print(
                "Note: in-memory graph is process-local. "
                "Offline eval does not need this step — use: agr-run-cases --no-llm",
                file=sys.stderr,
                flush=True,
            )
    finally:
        store.close()

