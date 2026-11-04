"""Isolated benchmark composition (B-03, B-05, C-05).

The demo ``eval`` profile loads seed triples and ordinary profiles may load local
indexes, so neither isolates a public corpus. This helper builds *fresh* stores
(never snapshots, never seed triples, never clearing an existing store) and one
``QueryService`` per AG arm from an immutable per-arm config copy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.config import AppConfig, Settings, resolve_path
from agentic_graphrag.eval.public.corpus import IntegrityError, is_forbidden_path
from agentic_graphrag.eval.public.enums import ArmKind
from agentic_graphrag.eval.public.manifest import RunManifest
from agentic_graphrag.eval.public.manifest_models import ArmSpec
from agentic_graphrag.governance.audit_store import AuditStore
from agentic_graphrag.knowledge.review.queue import ReviewQueue
from agentic_graphrag.stores.doc_store import InMemoryDocStore
from agentic_graphrag.stores.factory import GraphBackend, StoreBundle, VectorBackend
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore

BENCH_PROFILE = "bench"
BENCH_TENANT = ""  # passages are indexed unscoped inside an isolated bundle


def isolated_bundle() -> StoreBundle:
    """Fresh in-process stores; nothing is loaded from (or written to) developer data."""
    return StoreBundle(
        graph=InMemoryGraphStore(),
        vector=InMemoryVectorStore(),
        fulltext=BM25FulltextStore(),
        docs=InMemoryDocStore(),
        graph_backend=GraphBackend.MEMORY,
        vector_backend=VectorBackend.MEMORY,
    )


def workspace_path(manifest: RunManifest, *parts: str) -> Path:
    """Manifest-owned path; refuses developer/demo data directories."""
    root = resolve_path(manifest.workspace_dir)
    if is_forbidden_path(root, resolve_path(".")):
        raise IntegrityError(f"workspace {root} overlaps developer/demo data")
    return root.joinpath(*parts)


def arm_config(base: AppConfig, manifest: RunManifest, arm: ArmSpec, arm_dir: Path) -> AppConfig:
    """New AppConfig for one AG arm; the shared ``base`` is never mutated.

    The two AG arms differ only in ``requirement_register.enabled``.
    """
    if arm.kind is not ArmKind.INTERNAL_AG or arm.register_enabled is None:
        raise ValueError(f"{arm.name} is not an AG arm")
    data: dict[str, Any] = base.model_dump(mode="json")
    ctx = manifest.context
    guard = data["guardrails"]
    guard["requirement_register"]["enabled"] = arm.register_enabled
    guard.update(
        max_hops=ctx.max_hops,
        max_llm_calls=ctx.max_llm_calls_per_query,
        max_tokens_per_query=ctx.max_tokens_per_query,
        query_timeout_seconds=ctx.query_timeout_seconds,
    )
    models = manifest.models
    data["llm"]["strong_model"] = models.generator.id
    data["llm"]["embedding_model"] = models.embedding.id
    if models.verifier is not None:
        data["llm"]["light_model"] = models.verifier.id
    data["triage"]["enabled"] = False
    data["knowledge"]["schema_path"] = manifest.extraction_schema_path
    data["paths"].update(
        processed_dir=str(arm_dir / "runtime"),
        cache_dir=str(arm_dir / "cache"),
        indexes_dir=str(arm_dir / "indexes"),
    )
    return AppConfig.model_validate(data)


def trial_config(cfg: AppConfig, cache_dir: Path) -> AppConfig:
    """Per-trial LLM response-cache directory: no completion replay across questions.

    The query path always passes ``paths.cache_dir/llm`` to the provider; a fresh,
    trial-scoped directory keeps within-query reuse but blocks cross-query replay.
    """
    paths = cfg.paths.model_copy(update={"cache_dir": str(cache_dir)})
    return cfg.model_copy(update={"paths": paths})


def build_ag_service(
    bundle: StoreBundle,
    *,
    cfg: AppConfig,
    settings: Settings,
    known_entities: list[str],
    allow_llm: bool,
) -> QueryService:
    """QueryService over a prepared bundle: no seed graph, caches off, no triage."""
    runtime = resolve_path(cfg.paths.processed_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    return QueryService(
        cfg=cfg,
        settings=settings,
        bundle=bundle,
        allow_llm=allow_llm,
        known_entities=list(known_entities),
        enable_triage=False,
        enable_cache=False,
        audit_store=AuditStore(runtime / "audit_chains.jsonl"),
        review_queue=ReviewQueue(runtime / "review_queue.jsonl"),
        retrieval_cache=None,
        multi_budget=None,
        ingest_tasks=None,
        profile=BENCH_PROFILE,
    )


def check_prepared(
    passage_ids: frozenset[str], corpus_sha256: str, *, expected_ids: set[str], expected_sha256: str
) -> None:
    """Run before each arm: the prepared index must hold exactly the frozen corpus."""
    if corpus_sha256 != expected_sha256:
        raise IntegrityError("prepared index corpus hash differs from the frozen corpus")
    if set(passage_ids) != expected_ids:
        extra = len(set(passage_ids) - expected_ids)
        missing = len(expected_ids - set(passage_ids))
        raise IntegrityError(
            f"prepared index passages differ: {extra} unexpected, {missing} missing"
        )
