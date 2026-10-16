#!/usr/bin/env python3
"""Prune expired records from enterprise JSONL data files (ENT-06)."""

from __future__ import annotations

import argparse
import time

from agentic_graphrag.config import get_config, resolve_path
from agentic_graphrag.retention import PruneTarget, prune_jsonl


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for target in default_targets():
        result = prune_jsonl(target, now=time.time(), dry_run=args.dry_run)
        print(
            f"{result.path}: kept={result.kept} removed={result.removed} "
            f"malformed={result.malformed} dry_run={args.dry_run}"
        )


if __name__ == "__main__":
    main()
