"""Tenant-explicit repair, embedding rebuild and legacy identity migration."""

import argparse
import json

from agentic_graphrag.app.knowledge_maintenance import maintain_knowledge
from agentic_graphrag.core.knowledge import KnowledgeScope
from agentic_graphrag.knowledge.ingest_worker_cli import build_cli_service
from agentic_graphrag.stores.scope_codec import storage_tenant


def maintenance_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["reconcile", "reindex", "migrate-doc-ids", "prune-uploads"]
    )
    parser.add_argument("--tenant", required=True, help="Required ownership scope")
    parser.add_argument("--kb", default="default", help="Knowledge base within the tenant")
    parser.add_argument("--operation-id", help="Stable id for resuming a maintenance write")
    args = parser.parse_args(argv)
    if args.action == "prune-uploads":
        from agentic_graphrag.config import get_config, runtime_dir
        from agentic_graphrag.knowledge.resumable_upload import UploadSessions

        cfg = get_config()
        print(
            UploadSessions(runtime_dir(cfg) / "uploads", cfg.indexing).prune(
                tenant_id=storage_tenant(KnowledgeScope(tenant_id=args.tenant, kb_id=args.kb))
            )
        )
        return
    svc = build_cli_service()
    try:
        result = maintain_knowledge(
            svc,
            tenant_id=args.tenant,
            kb_id=args.kb,
            action=args.action,
            operation_id=args.operation_id,
        )
    finally:
        svc.close()
    print(json.dumps(result))
    if result["status"] == "failed":
        raise SystemExit(1)


def reconcile_main(argv: list[str]) -> None:
    maintenance_main(["reconcile", *argv])


def reindex_main(argv: list[str]) -> None:
    maintenance_main(["reindex", *argv])


def migrate_main(argv: list[str]) -> None:
    maintenance_main(["migrate-doc-ids", *argv])


if __name__ == "__main__":
    maintenance_main()
