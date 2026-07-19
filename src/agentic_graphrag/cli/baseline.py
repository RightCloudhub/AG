"""Baseline vector RAG command (P2-EV-03)."""

from __future__ import annotations

import argparse
import json

from agentic_graphrag.config import get_config, resolve_path


def run_baseline_main(argv: list[str] | None = None) -> None:
    """P2-EV-03 — pure vector RAG baseline on interim / temp corpus."""
    parser = argparse.ArgumentParser(
        description="Run baseline vector RAG (no graph, single-shot retrieval)"
    )
    parser.add_argument("--cases", default=None, help="Eval cases JSONL")
    parser.add_argument(
        "--chunks",
        default=None,
        help="chunks.jsonl (default: data/processed/chunks.jsonl)",
    )
    parser.add_argument(
        "--raw-docs",
        default=None,
        help="Fallback raw docs dir when chunks missing (default: data/raw)",
    )
    parser.add_argument(
        "--embeddings",
        default=None,
        help="Optional embeddings.jsonl cache",
    )
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--no-llm", action="store_true", help="Offline extractive baseline")
    parser.add_argument(
        "--out",
        default=None,
        help="Report dir (writes baseline_run.jsonl + baseline_accuracy.json)",
    )
    args = parser.parse_args(argv)
    cfg = get_config()
    cases_path = resolve_path(args.cases or cfg.eval.cases_path)
    report_dir = resolve_path(args.out or cfg.eval.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    from agentic_graphrag.eval.baseline_rag import (
        build_baseline_pipeline,
        run_baseline_cases,
        write_baseline_report,
    )
    from agentic_graphrag.eval.scoring import write_accuracy_summary

    allow_llm = not args.no_llm
    pipeline, chunks = build_baseline_pipeline(
        cfg=cfg,
        chunks_path=args.chunks,
        raw_docs_dir=args.raw_docs,
        embeddings_path=args.embeddings,
        allow_llm=allow_llm,
        top_k=args.top_k,
    )
    print(f"Baseline corpus: {len(chunks)} chunks (allow_llm={pipeline.allow_llm})")

    cases = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    results = run_baseline_cases(cases, pipeline)
    report_path = report_dir / "baseline_run.jsonl"
    write_baseline_report(results, report_path)
    print(f"Baseline report → {report_path}")

    acc_path = report_dir / "baseline_accuracy.json"
    acc = write_accuracy_summary(report_path, acc_path)
    print(f"Baseline accuracy: {acc.correct}/{acc.total} = {acc.accuracy * 100:.1f}% → {acc_path}")

