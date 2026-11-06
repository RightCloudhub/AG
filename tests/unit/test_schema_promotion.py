"""Conservative drafts, explicit human mappings and recoverable shadow publication."""

from dataclasses import replace

import pytest
from test_knowledge_scope_stores import raw_bundle
from test_open_graph import OpenLLM, chunk, payload

from agentic_graphrag.core.knowledge import KnowledgeBase, KnowledgeScope
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.core.schema_drafts import DraftApproval
from agentic_graphrag.knowledge.generation_pipeline import GenerationPipeline
from agentic_graphrag.knowledge.graph_ingest import GraphIngestDeps, GraphIngestor
from agentic_graphrag.knowledge.schema_drafts import draft_from_graph
from agentic_graphrag.knowledge.schema_promotion import SchemaPromoter, approve_draft
from agentic_graphrag.knowledge.schema_registry import schema_for_knowledge_base
from agentic_graphrag.stores.entity_index import SQLiteEntityIndex
from agentic_graphrag.stores.knowledge_store import SQLiteKnowledgeStore
from agentic_graphrag.stores.schema_registry import SQLiteSchemaRegistry
from agentic_graphrag.stores.scoped_bundle import scope_bundle
from agentic_graphrag.stores.snapshot_bundle import snapshot_bundle


def setup_promotion(tmp_path):
    raw = raw_bundle()
    kb = KnowledgeBase(tenant_id="t", kb_id="contracts", build_mode="open")
    metadata = SQLiteKnowledgeStore(tmp_path / "meta.sqlite3")
    metadata.create(kb)
    bound = scope_bundle(raw, kb.scope)
    entity_index = SQLiteEntityIndex(tmp_path / "entities.sqlite3")
    llm = OpenLLM(payload())
    source = chunk(kb)
    bound.docs.save(
        DocumentRecord(
            doc_id=source.doc_id,
            title="Contract",
            content=source.text,
            tenant_id=kb.tenant_id,
            kb_id=kb.kb_id,
            chunks=[source],
            content_sha256=source.metadata["content_sha256"],
            metadata={"index_complete": True},
        )
    )
    ingest = GraphIngestor(
        GraphIngestDeps(
            graph=bound.graph,
            llm=llm,
            knowledge_base=kb,
            entity_index=entity_index,
            embedder=llm,
            embedding_space="v1",
        )
    )
    ingest.ingest_chunks([source], tenant_id="t")
    registry = SQLiteSchemaRegistry(tmp_path / "schemas.sqlite3")
    draft = registry.save_draft(draft_from_graph(bound.graph, kb))
    pipeline = GenerationPipeline(raw, metadata)
    return kb, bound, metadata, registry, draft, SchemaPromoter(pipeline, registry)


def approval(draft, *, entity_mapping=None, relation_mapping=None):
    return DraftApproval(
        schema_id="reviewed",
        schema_version="1",
        reviewer="human-reviewer",
        schema_definition=draft.schema_definition,
        entity_mapping=draft.entity_mapping if entity_mapping is None else entity_mapping,
        relation_mapping=draft.relation_mapping if relation_mapping is None else relation_mapping,
    )


def test_duplicate_chunk_relations_do_not_inflate_document_support(tmp_path):
    kb, bound, _meta, _registry, _draft, _promoter = setup_promotion(tmp_path)
    relation = bound.graph.list_relations()[0]
    bound.graph.upsert_relations([replace(relation, id="duplicate-chunk")])
    draft = draft_from_graph(bound.graph, kb)
    assert len(draft.relations) == 1
    proposal = draft.relations[0]
    assert proposal.document_support == 1 and proposal.distinct_pairs == 1
    assert len(proposal.relation_ids) == 2
    assert proposal.examples[0].doc_version == 1
    assert all(
        "cardinality" not in rel for rel in draft.schema_definition["relation_types"].values()
    )


def test_human_approval_promotes_schema_and_rollback_restores_open_config(tmp_path):
    kb, _bound, meta, registry, draft, promoter = setup_promotion(tmp_path)
    with pytest.raises(ValueError, match="human-approved"):
        promoter.promote(kb.scope, draft.draft_id, operation_id="promote")
    approved = approve_draft(registry, kb.scope, draft.draft_id, approval=approval(draft))
    assert approved.reviewer == "human-reviewer"
    published = promoter.promote(kb.scope, draft.draft_id, operation_id="promote")
    assert published.build_mode == "schema" and published.rule_planner_profile is None
    assert published.schema_id == "reviewed"
    schema = schema_for_knowledge_base(published, registry)
    graph = snapshot_bundle(meta, kb.scope, published.active_generation).graph
    assert all(row.type in schema.relation_types for row in graph.list_relations())
    assert promoter.promote(kb.scope, draft.draft_id, operation_id="promote") == published
    restored = meta.publish(kb.scope, generation="1", expected_revision=published.config_revision)
    assert restored.build_mode == "open" and restored.schema_id is None
    assert snapshot_bundle(meta, kb.scope, "1").graph.list_relations()[0].type == "负责审核（合同）"


def test_incomplete_mapping_is_explicit_and_preserves_active_open_graph(tmp_path):
    kb, bound, meta, registry, draft, promoter = setup_promotion(tmp_path)
    approve_draft(registry, kb.scope, draft.draft_id, approval=approval(draft, relation_mapping={}))
    with pytest.raises(ValueError, match="unresolved"):
        promoter.promote(kb.scope, draft.draft_id, operation_id="promote")
    assert meta.get(kb.scope).active_generation == "1"
    assert bound.graph.list_relations()[0].type == "负责审核（合同）"


def test_review_is_scoped_and_schema_versions_are_immutable(tmp_path):
    kb, _bound, _meta, registry, draft, _promoter = setup_promotion(tmp_path)
    other = KnowledgeScope(tenant_id="another", kb_id=kb.kb_id)
    assert registry.get_draft(other, draft.draft_id) is None
    with pytest.raises(KeyError):
        approve_draft(registry, other, draft.draft_id, approval=approval(draft))
    approve_draft(registry, kb.scope, draft.draft_id, approval=approval(draft))
    assert registry.get_schema(other, schema_id="reviewed", version="1") is None
    assert registry.list_drafts(other) == []
    with pytest.raises(ValueError, match="already"):
        approve_draft(registry, kb.scope, draft.draft_id, approval=approval(draft))


def test_graph_mutation_after_draft_refuses_publication(tmp_path):
    kb, bound, meta, registry, draft, promoter = setup_promotion(tmp_path)
    approve_draft(registry, kb.scope, draft.draft_id, approval=approval(draft))
    relation = bound.graph.list_relations()[0]
    bound.graph.upsert_relations([replace(relation, id="new-fact")])
    with pytest.raises(ValueError, match="changed after"):
        promoter.promote(kb.scope, draft.draft_id, operation_id="promote")
    assert meta.get(kb.scope).build_mode == "open"


def test_crash_after_shadow_conversion_can_resume_without_losing_source(tmp_path, monkeypatch):
    kb, _bound, meta, registry, draft, promoter = setup_promotion(tmp_path)
    approve_draft(registry, kb.scope, draft.draft_id, approval=approval(draft))
    original = promoter.pipeline.complete

    def crash(*args, **kwargs):
        raise RuntimeError("simulated worker crash")

    monkeypatch.setattr(promoter.pipeline, "complete", crash)
    with pytest.raises(RuntimeError, match="worker crash"):
        promoter.promote(kb.scope, draft.draft_id, operation_id="promote")
    assert meta.get(kb.scope).build_mode == "open"
    monkeypatch.setattr(promoter.pipeline, "complete", original)
    assert promoter.promote(kb.scope, draft.draft_id, operation_id="promote").build_mode == "schema"
