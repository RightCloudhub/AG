"""Build-graph command (extract + load)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentic_graphrag.cli._common import _open_graph_store
from agentic_graphrag.config import AppConfig, Settings, get_config, get_settings, resolve_path
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph
from agentic_graphrag.knowledge.schema_check import (
    SchemaDefinition,
    Triple,
    gate_triples,
    load_schema,
)
from agentic_graphrag.stores.interfaces import ChunkRecord


def build_graph_main(argv: list[str] | None = None) -> None:
    args = _parse_build_graph(argv)
    cfg = get_config()
    settings = get_settings()
    schema = load_schema(resolve_path(cfg.knowledge.schema_path))
    triples = _load_or_extract_triples(args, cfg, settings, schema=schema)
    triples, gate_rejected = _gate_and_report(triples, schema, cfg)
    _write_graph(args, settings, triples, gate_rejected=gate_rejected)


def _parse_build_graph(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract triples and load graph (Neo4j by default; --memory-graph for offline)"
    )
    parser.add_argument("--chunks", default=None)
    parser.add_argument("--triples", default=None)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--memory-graph", action="store_true")
    parser.add_argument("--no-clear", action="store_true")
    return parser.parse_args(argv)


def _load_or_extract_triples(
    args: argparse.Namespace,
    cfg: AppConfig,
    settings: Settings,
    *,
    schema: SchemaDefinition,
) -> list[Triple]:
    if args.triples:
        return [
            Triple.model_validate(json.loads(line))
            for line in resolve_path(args.triples).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    if args.no_llm:
        print("--no-llm requires --triples", file=sys.stderr)
        sys.exit(2)
    return _extract_triples(args, cfg=cfg, settings=settings, schema=schema)


def _extract_triples(
    args: argparse.Namespace,
    *,
    cfg: AppConfig,
    settings: Settings,
    schema: SchemaDefinition,
) -> list[Triple]:
    from agentic_graphrag.config import build_llm_provider
    from agentic_graphrag.knowledge.extraction import RetryPolicy, run_extract_pipeline
    from agentic_graphrag.llm.budget import BudgetTracker
    from agentic_graphrag.stores.doc_store import FileDocStore

    chunks_path = resolve_path(args.chunks or f"{cfg.paths.processed_dir}/chunks.jsonl")
    chunks = [
        ChunkRecord(**json.loads(line))
        for line in chunks_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    budget = BudgetTracker(max_llm_calls=10_000, max_tokens=10_000_000)
    llm = build_llm_provider(
        budget=budget,
        cache_dir=resolve_path(cfg.paths.cache_dir) / "llm",
        settings=settings,
        cfg=cfg,
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
    _write_rejected(pipe.rejected, cfg)
    print(
        f"Extracted accepted={len(pipe.accepted)} rejected={len(pipe.rejected)} "
        f"ok_chunks={pipe.ok_count} failed={pipe.failed_count} skipped={pipe.skipped_count}"
    )
    triples_path = resolve_path(f"{cfg.paths.processed_dir}/triples.jsonl")
    with triples_path.open("w", encoding="utf-8") as f:
        for t in pipe.accepted:
            f.write(json.dumps(t.model_dump(), ensure_ascii=False) + "\n")
    return pipe.accepted


def _write_rejected(rejected: list, cfg: AppConfig) -> Path:
    reject_path = resolve_path(f"{cfg.paths.processed_dir}/rejected_triples.jsonl")
    with reject_path.open("w", encoding="utf-8") as f:
        for t, reason in rejected:
            f.write(
                json.dumps({"triple": t.model_dump(), "reason": reason}, ensure_ascii=False) + "\n"
            )
    return reject_path


def _gate_and_report(
    triples: list[Triple], schema: SchemaDefinition, cfg: AppConfig
) -> tuple[list[Triple], int]:
    thr = cfg.knowledge.extract_confidence_threshold
    gated = gate_triples(triples, schema, confidence_threshold=thr)
    reject_path = _write_rejected(gated.rejected, cfg)
    print(
        f"Ingestion gate: accepted={len(gated.accepted)} rejected={len(gated.rejected)} "
        f"(threshold={thr}) → {reject_path}",
        flush=True,
    )
    if gated.rejection_reasons:
        print(f"Rejection reasons: {gated.rejection_reasons}", flush=True)
    return gated.accepted, len(gated.rejected)


def _write_graph(
    args: argparse.Namespace,
    settings: Settings,
    triples: list[Triple],
    *,
    gate_rejected: int,
) -> None:
    store, backend = _open_graph_store(
        settings,
        memory=args.memory_graph,
        allow_memory_fallback=args.no_llm,
    )
    try:
        stats = load_triples_into_graph(store, triples, clear_first=not args.no_clear)
        stats["backend"] = backend
        stats["gate_rejected"] = gate_rejected
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
