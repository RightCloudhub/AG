"""Carry canonical entity candidates forward without reusing withdrawn descriptions."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from agentic_graphrag.core.knowledge import SourceRef
from agentic_graphrag.core.ports.entities import EntityEmbedder, EntitySemanticIndex, SemanticEntity
from agentic_graphrag.core.ports.stores import EntityRecord
from agentic_graphrag.stores.scoped_graph_scan import entities_in_scope

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def seed_entities(
    svc: QueryService, *, index: EntitySemanticIndex, embedder: EntityEmbedder | None = None
) -> None:
    from agentic_graphrag.app.graph_wiring import entity_space

    kb, metadata = svc.knowledge_base, svc.knowledge_store
    if kb is None or metadata is None:
        return
    write = next(
        (
            row
            for row in metadata.pending_writes(kb.scope)
            if row.target_generation == kb.active_generation
        ),
        None,
    )
    if write is None:
        return
    target_space = entity_space(svc)
    source_space = _source_space(svc, generation=write.source_generation)
    retained: list[SemanticEntity] = []
    for entity in entities_in_scope(svc.bundle.graph, kb.tenant_id):
        existing = index.get(entity.id, scope=kb.scope, space=target_space)
        if existing is not None:
            retained.append(existing)
            continue
        old = index.get(entity.id, scope=kb.scope, space=source_space)
        reusable = old if source_space.rsplit(":", 1)[0] == target_space.rsplit(":", 1)[0] else None
        current = _semantic(entity, old=reusable, svc=svc, embedder=embedder)
        if current is None:
            continue
        index.upsert([current], space=target_space)
        retained.append(current)
    index.copy_identity(
        retained, scope=kb.scope, source_space=source_space, target_space=target_space
    )


def _source_space(svc: QueryService, *, generation: str) -> str:
    from agentic_graphrag.app.graph_wiring import entity_space
    from agentic_graphrag.app.model_config import effective_model_config

    kb, metadata = svc.knowledge_base, svc.knowledge_store
    assert kb is not None and metadata is not None
    source = metadata.get_snapshot(kb.scope, generation)
    before = source[0].knowledge_base if source else None
    if before is None:
        return entity_space(svc, generation=generation)
    cfg = effective_model_config(
        (svc.parent or svc).cfg,
        svc.settings,
        tenant_id=kb.tenant_id,
        kb_id=kb.kb_id,
        profile_id=before.model_profile_id,
        profile_revision=before.model_profile_revision,
    )
    return entity_space(replace(svc, cfg=cfg), generation=generation)


def _semantic(
    entity: EntityRecord,
    *,
    old: SemanticEntity | None,
    svc: QueryService,
    embedder: EntityEmbedder | None,
) -> SemanticEntity | None:
    kb = svc.knowledge_base
    assert kb is not None
    sources = tuple(SourceRef.model_validate(raw) for raw in entity.sources)
    description = str(entity.attributes.get("description") or entity.name)
    if old is not None and old.description == description:
        return old.model_copy(
            update={
                "sources": sources,
                "aliases": tuple(entity.aliases),
                "name": entity.name,
                "entity_type": entity.type,
            }
        )
    if embedder is None:
        # Changed descriptions are re-embedded by the next model-backed ingestor.
        # Never carry vectors or aliases derived from withdrawn descriptions.
        return None
    vectors = embedder.embed_many([description])
    if len(vectors) != 1:
        raise ValueError("Entity embedding returned the wrong number of vectors")
    return SemanticEntity(
        scope=kb.scope,
        entity_id=entity.id,
        name=entity.name,
        entity_type=entity.type,
        description=description,
        aliases=tuple(entity.aliases),
        vector=tuple(vectors[0]),
        sources=sources,
    )


def carry_entities(svc: QueryService) -> None:
    from agentic_graphrag.app.graph_wiring import entity_index_for

    if svc.knowledge_base is not None and svc.knowledge_base.build_mode == "open":
        seed_entities(svc, index=entity_index_for(svc))
