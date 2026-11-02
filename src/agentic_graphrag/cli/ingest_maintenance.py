"""Tenant-explicit repair, embedding rebuild and legacy identity migration."""

import argparse
import json
from dataclasses import asdict

from agentic_graphrag.knowledge.ingest_worker_cli import build_cli_worker


def maintenance_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["reconcile", "reindex", "migrate-doc-ids", "prune-uploads"]
    )
    parser.add_argument("--tenant", required=True, help="Required ownership scope")
    args = parser.parse_args(argv)
    if args.action == "prune-uploads":
        from agentic_graphrag.config import get_config, runtime_dir
        from agentic_graphrag.knowledge.resumable_upload import UploadSessions

        cfg = get_config()
        print(
            UploadSessions(runtime_dir(cfg) / "uploads", cfg.indexing).prune(tenant_id=args.tenant)
        )
        return
    worker = build_cli_worker()
    if args.action == "migrate-doc-ids":
        rows = worker.pipeline.lifecycle.migrate(tenant_id=args.tenant)
        if worker.task_store:
            worker.task_store.remap_documents(
                {r["old_id"]: r["doc_id"] for r in rows}, tenant_id=args.tenant
            )
        print(json.dumps(rows))
        worker.reconcile(tenant_id=args.tenant)
        return
    results = (
        worker.reconcile(tenant_id=args.tenant)
        if args.action == "reconcile"
        else worker.reindex(tenant_id=args.tenant)
    )
    print(json.dumps([asdict(result) for result in results]))
    if any(result.error for result in results):
        raise SystemExit(1)


def reconcile_main(argv: list[str]) -> None:
    maintenance_main(["reconcile", *argv])


def reindex_main(argv: list[str]) -> None:
    maintenance_main(["reindex", *argv])


def migrate_main(argv: list[str]) -> None:
    maintenance_main(["migrate-doc-ids", *argv])


if __name__ == "__main__":
    maintenance_main()
