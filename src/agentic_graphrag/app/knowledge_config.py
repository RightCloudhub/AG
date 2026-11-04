"""Pin model profiles and publish configuration changes through complete shadow indexes."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

from agentic_graphrag.app.entity_generations import carry_entities
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.app.model_config import effective_model_config
from agentic_graphrag.app.model_profiles import ModelProfiles
from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.knowledge.generation_pipeline import GenerationPipeline

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService

_VERSIONED_CONFIG = (
    "model_profile_id",
    "model_profile_revision",
    "review_policy",
    "global_summary_enabled",
)


def pin_profile(svc: QueryService, kb: KnowledgeBase) -> KnowledgeBase:
    if kb.build_mode == "open" and kb.review_policy == "required":
        raise ApiError(
            "INVALID_INPUT", "Required review is only supported in Schema mode", status_code=422
        )
    if kb.model_profile_id is None:
        return kb.model_copy(update={"model_profile_revision": None})
    profiles = ModelProfiles(svc.cfg, svc.settings)
    profile = (
        profiles.revision(
            kb.model_profile_id, tenant_id=kb.tenant_id, revision=kb.model_profile_revision
        )
        if kb.model_profile_revision is not None
        else profiles.active(kb.model_profile_id, tenant_id=kb.tenant_id)
    )
    return kb.model_copy(update={"model_profile_revision": profile.revision})


def update_configuration(
    svc: QueryService,
    proposed: KnowledgeBase,
    *,
    expected_revision: int,
    operation_id: str | None = None,
) -> KnowledgeBase:
    metadata = knowledge_store(svc)
    current = metadata.get(proposed.scope)
    if current is None or current.config_revision != expected_revision:
        raise ValueError("Knowledge base configuration revision conflict")
    proposed = pin_profile(svc, proposed)
    if all(getattr(current, key) == getattr(proposed, key) for key in _VERSIONED_CONFIG):
        return metadata.update(proposed, expected_revision=expected_revision)
    selected = select_service(svc, tenant_id=current.tenant_id, kb_id=current.kb_id)
    cfg = effective_model_config(
        svc.cfg,
        svc.settings,
        tenant_id=current.tenant_id,
        kb_id=current.kb_id,
        profile_id=proposed.model_profile_id,
        profile_revision=proposed.model_profile_revision,
    )
    changed_embedding = selected.cfg.llm.embedding_model != cfg.llm.embedding_model
    _check_embedding_capability(selected, changed=changed_embedding)
    pipeline = GenerationPipeline(svc.bundle, metadata)
    write = pipeline.prepare(current.scope, operation_id=operation_id or f"config-{uuid4()}")
    shadow = replace(
        selected,
        cfg=cfg,
        bundle=write.bundle,
        knowledge_base=proposed.model_copy(update={"active_generation": write.target_generation}),
        enable_cache=False,
    )
    if changed_embedding:
        _reindex(shadow)
    else:
        carry_entities(shadow)
    published = pipeline.complete(write, target_config=proposed)
    if svc.retrieval_cache is not None:
        svc.retrieval_cache.on_index_update()
    return published


def _check_embedding_capability(svc: QueryService, *, changed: bool) -> None:
    kb = svc.knowledge_base
    assert kb is not None
    if changed and not svc.allow_llm and svc.bundle.docs.list_ids(tenant_id=kb.tenant_id):
        raise ApiError(
            "CAPABILITY_UNAVAILABLE",
            "Embedding changes require enabled models and a complete reindex",
            status_code=503,
        )


def _reindex(svc: QueryService) -> None:
    from agentic_graphrag.app.write_path import build_inline_worker

    kb = svc.knowledge_base
    assert kb is not None
    if not svc.bundle.docs.list_ids(tenant_id=kb.tenant_id):
        return
    worker = build_inline_worker(svc, route_tasks=False)
    try:
        results = worker.reindex(tenant_id=kb.tenant_id)
        if any(result.error for result in results):
            raise ValueError("Embedding rebuild failed; the previous generation is still active")
    finally:
        client = worker.graph_ingestor.llm if worker.graph_ingestor else None
        if (close := getattr(client, "close", None)) is not None:
            close()
