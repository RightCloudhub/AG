"""Canonical identity survives configuration publication and later human merges."""

import pytest
from test_knowledge_workflow import root_service as root_service

from agentic_graphrag.app.graph_wiring import entity_index_for, entity_space
from agentic_graphrag.app.knowledge_config import update_configuration
from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.app.review_app import ReviewApp
from agentic_graphrag.core.knowledge import KnowledgeBase, SourceRef
from agentic_graphrag.core.ports.entities import SemanticEntity
from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord, EntityRecord
from agentic_graphrag.knowledge.chunk_sources import stamp_chunk_sources
from agentic_graphrag.knowledge.review.queue import ReviewType


def setup_entities(svc):
    kb = knowledge_store(svc).create(
        KnowledgeBase(kb_id="open", build_mode="open", rule_planner_profile=None)
    )
    bound = select_service(svc, tenant_id=kb.tenant_id, kb_id=kb.kb_id)
    doc = DocumentRecord("d", "Original", "Alice and A. lead separate teams.")
    doc.metadata["index_complete"] = True
    doc.chunks = [ChunkRecord("c", "d", doc.content, 0, fulltext_indexed=True)]
    doc.tenant_id, doc.kb_id = kb.tenant_id, kb.kb_id
    stamp_chunk_sources(doc)
    bound.bundle.docs.save(doc)
    ref = SourceRef(
        scope=kb.scope, doc_id="d", doc_version=1, chunk_id="c", content_sha256=doc.content_sha256
    )
    index, space = entity_index_for(bound), entity_space(bound)
    entities = [
        EntityRecord(key, name, "Person", sources=[ref.model_dump(mode="json")])
        for key, name in (("alice", "Alice"), ("alias", "A."))
    ]
    bound.bundle.graph.upsert_entities(entities)
    index.upsert(
        [
            SemanticEntity(
                scope=kb.scope,
                entity_id=e.id,
                name=e.name,
                description=e.name,
                entity_type=e.type,
                vector=(1.0, 0.0),
                sources=(ref,),
            )
            for e in entities
        ],
        space=space,
    )
    index.confirm_alias("verified Alice", "alice", scope=kb.scope, space=space)
    index.bind_mention("mention", "alice", source=ref, space=space)
    # Preserve a historic ID whose canonical entity is still present.
    index.upsert(
        [
            SemanticEntity(
                scope=kb.scope,
                entity_id="old-alice",
                name="Former",
                description="Former",
                entity_type="Person",
                vector=(1.0, 0.0),
                sources=(ref,),
            )
        ],
        space=space,
    )
    index.redirect("old-alice", "alice", scope=kb.scope, space=space, operation_id="old-merge")
    return bound, index, ref


def test_configuration_carries_confirmed_alias_mentions_redirects_without_models(root_service):
    bound, index, _ = setup_entities(root_service)
    proposed = bound.knowledge_base.model_copy(update={"global_summary_enabled": True})
    published = update_configuration(root_service, proposed, expected_revision=1)
    current = select_service(root_service, tenant_id="default", kb_id="open")
    space = entity_space(current)
    assert index.confirmed_entity("verified Alice", scope=published.scope, space=space) == "alice"
    assert index.mention("mention", scope=published.scope, space=space) == "alice"
    assert index.get("old-alice", scope=published.scope, space=space).entity_id == "alice"


def test_review_after_publication_has_the_semantic_records_to_merge(root_service):
    bound, index, ref = setup_entities(root_service)
    proposed = bound.knowledge_base.model_copy(update={"global_summary_enabled": True})
    update_configuration(root_service, proposed, expected_revision=1)
    current = select_service(root_service, tenant_id="default", kb_id="open")
    previous = current.knowledge_base.active_generation
    item = current.review_queue.enqueue(
        ReviewType.RESOLUTION,
        {
            "open_resolution": True,
            "keep_id": "alice",
            "drop_id": "alias",
            "candidate_ids": ["alice"],
            "kb_id": "open",
            "source_refs": [ref.model_dump(mode="json")],
        },
        tenant_id="default",
    )
    result = ReviewApp(current).decide(item.id, "approve", tenant_id="default", reviewer="human")
    assert result.execution["effect"] == "entities_merged"
    current = select_service(root_service, tenant_id="default", kb_id="open")
    assert current.knowledge_base.active_generation != previous
    assert current.bundle.graph.counts()["nodes"] == 1
    assert index.get("alias", scope=ref.scope, space=entity_space(current)).entity_id == "alice"


def test_failed_embedding_rebuild_does_not_change_active_generation(root_service, monkeypatch):
    bound, _index, _ = setup_entities(root_service)
    proposed = bound.knowledge_base.model_copy(update={"global_summary_enabled": True})
    from agentic_graphrag.app import knowledge_config

    real_config = knowledge_config.effective_model_config

    def changed(*args, **kwargs):
        cfg = real_config(*args, **kwargs)
        cfg.llm.embedding_model = "new-space"
        return cfg

    def failure(svc):
        raise OSError("embedding provider unavailable")

    root_service.allow_llm = True
    monkeypatch.setattr(knowledge_config, "effective_model_config", changed)
    monkeypatch.setattr(knowledge_config, "_reindex", failure)
    with pytest.raises(OSError):
        update_configuration(root_service, proposed, expected_revision=1)
    assert knowledge_store(root_service).get(proposed.scope).active_generation == "1"
