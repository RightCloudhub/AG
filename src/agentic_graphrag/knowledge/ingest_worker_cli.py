"""``python -m agentic_graphrag.knowledge.ingest_worker`` entry point.

Wires the worker the way the docs promise: consuming the upload task queue
(BL-05) and writing extracted facts into the knowledge graph (BL-01). With
``AGR_USE_LIVE_STORES=1`` it shares Neo4j/Qdrant with the API; offline, the
graph and vectors are process-local, so prefer the API's inline worker
(``AGR_INGEST_INLINE=1``) there.
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import TYPE_CHECKING

from agentic_graphrag.knowledge.ingest_worker import IngestWorker

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def build_cli_worker() -> IngestWorker:
    from agentic_graphrag.app.write_path import build_inline_worker

    return build_inline_worker(build_cli_service())


def build_cli_service() -> QueryService:
    from agentic_graphrag.app.query_service import QueryService
    from agentic_graphrag.bootstrap import build_stores, resolve_runtime_profile
    from agentic_graphrag.config import get_config, get_settings

    cfg = get_config()
    settings = get_settings()
    rp = resolve_runtime_profile(
        llm_api_key=settings.llm_api_key,
        llm_auth_required=settings.llm_auth_required,
        model_roles_configured=bool(cfg.models.roles),
    )
    bundle = build_stores(rp, cfg=cfg, settings=settings)
    if not bundle.docs.durable:
        logger.warning(
            "Doc store %s is process-local: this worker cannot see documents uploaded "
            "through the API. Configure a file-backed doc store before relying on it.",
            type(bundle.docs).__name__,
        )
    if getattr(bundle.fulltext, "path", None) is None:
        logger.warning(
            "Fulltext store %s is process-local: chunks this worker indexes are invisible "
            "to the API. Use AGR_PROFILE=live|hybrid (shared BM25 snapshot).",
            type(bundle.fulltext).__name__,
        )
    svc = QueryService._from_bundle(
        bundle,
        cfg=cfg,
        settings=settings,
        seed_triples="",
        load_seed=False,
        allow_llm=rp.allow_llm,
    )
    return svc


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run ingest worker")
    parser.add_argument("--once", action="store_true", help="Single poll then exit")
    args = parser.parse_args(argv)
    worker = build_cli_worker()
    if args.once:
        for r in worker.run_once():
            facts = (r.graph or {}).get("accepted", 0)
            print(
                f"  {r.doc_id}: {r.chunks_created} chunks, {r.chunks_indexed} indexed, "
                f"{facts} graph facts"
            )
        return
    worker.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        worker.stop()
