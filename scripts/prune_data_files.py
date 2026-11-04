#!/usr/bin/env python3
"""Prune expired records from enterprise JSONL files and the doc store (ENT-06/K-06)."""

from __future__ import annotations

import argparse
import time

from agentic_graphrag.config import get_config, resolve_path, runtime_dir
from agentic_graphrag.retention import PruneTarget, prune_jsonl
from agentic_graphrag.stores.doc_retention import prune_doc_store
from agentic_graphrag.stores.doc_store import FileDocStore


def default_targets() -> list[PruneTarget]:
    cfg = get_config()
    root = resolve_path(cfg.paths.processed_dir)
    retention = cfg.retention
    return [
        PruneTarget(root / "audit_chains.jsonl", retention.audit_chains_days, ("created_at",)),
        PruneTarget(root / "audit_events.jsonl", retention.audit_events_days, ("ts",)),
        PruneTarget(
            root / "review_queue.jsonl",
            retention.review_queue_days,
            ("decided_at", "created_at"),
        ),
        PruneTarget(root / "ingest_tasks.jsonl", retention.ingest_tasks_days, ("updated_at",)),
    ]


def prune_documents(*, now: float, dry_run: bool) -> dict[str, int]:
    """Doc-store operation records and retained original sources (K-06)."""
    cfg = get_config()
    docs_root = runtime_dir(cfg) / "docs"
    if not (docs_root / "documents.sqlite3").exists():
        return {}
    return prune_doc_store(FileDocStore(docs_root), cfg.retention, now=now, dry_run=dry_run)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    now = time.time()
    for target in default_targets():
        result = prune_jsonl(target, now=now, dry_run=args.dry_run)
        print(
            f"{result.path}: kept={result.kept} removed={result.removed} "
            f"malformed={result.malformed} dry_run={args.dry_run}"
        )
    counts = prune_documents(now=now, dry_run=args.dry_run)
    summary = " ".join(f"{name}={count}" for name, count in sorted(counts.items()))
    print(f"documents: {summary or 'no doc store'} dry_run={args.dry_run}")


if __name__ == "__main__":
    main()
