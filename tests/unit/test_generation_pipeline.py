"""Generation writes publish only complete, sealed, non-stale index snapshots."""

from dataclasses import replace

import pytest
from test_knowledge_scope_stores import populate, raw_bundle

from agentic_graphrag.core.knowledge import KnowledgeBase, KnowledgeScope
from agentic_graphrag.core.ports.stores import EntityRecord
from agentic_graphrag.knowledge.chunk_sources import stamp_chunk_sources
from agentic_graphrag.knowledge.generation_pipeline import GenerationPipeline
from agentic_graphrag.stores.doc_store import FileDocStore
from agentic_graphrag.stores.knowledge_store import SQLiteKnowledgeStore
from agentic_graphrag.stores.scoped_bundle import scope_bundle
from agentic_graphrag.stores.snapshot_bundle import snapshot_bundle


def setup_generation(tmp_path):
    raw = raw_bundle()
    scope = KnowledgeScope(tenant_id="tenant", kb_id="contracts")
    meta = SQLiteKnowledgeStore(tmp_path / "meta.db")
    meta.create(KnowledgeBase(tenant_id="tenant", kb_id="contracts"))
    populate(scope_bundle(raw, scope), scope, content="first risk")
    return raw, scope, meta, GenerationPipeline(raw, meta)


def mark_ready(bundle):
    doc = bundle.docs.get("doc", tenant_id="tenant")
    doc.metadata["index_complete"] = True
    bundle.docs.save(doc)


def test_shadow_generation_is_hidden_until_atomic_publish(tmp_path):
    raw, scope, meta, pipeline = setup_generation(tmp_path)
    write = pipeline.prepare(scope, operation_id="upload")
    assert meta.get(scope).active_generation == "1"
    assert write.bundle.docs.get("doc").content == "first risk"
    write.bundle.graph.delete_by_doc("doc", "tenant")
    populate(write.bundle, scope, content="second risk", version=2)
    with pytest.raises(ValueError, match="finish indexing"):
        pipeline.complete(write)
    assert snapshot_bundle(meta, scope, "1").docs.get("doc").content == "first risk"
    assert meta.pending_writes(scope)[0].stage == "seeded"
    mark_ready(write.bundle)
    kb = pipeline.complete(write)
    assert kb.active_generation == write.target_generation
    assert kb.config_revision == 2
    assert not meta.pending_writes(scope)
    assert (
        snapshot_bundle(meta, scope, kb.active_generation).docs.get("doc").content == "second risk"
    )
    changes = meta.changes(scope)
    assert len(changes) == 1
    assert changes[0].document_ids == ("doc",)
    assert changes[0].relation_upserts == ("r",)
    assert changes[0].source_retractions == ("doc",)
    assert not changes[0].topology_changed
    assert pipeline.complete(write) == kb
    assert len(meta.changes(scope)) == 1
    retry = pipeline.prepare(scope, operation_id="upload")
    with pytest.raises(ValueError, match="read-only"):
        retry.bundle.graph.upsert_entities([])


def test_stale_writer_never_overwrites_new_generation(tmp_path):
    raw, scope, meta, pipeline = setup_generation(tmp_path)
    first = pipeline.prepare(scope, operation_id="first")
    second = pipeline.prepare(scope, operation_id="second")
    first.bundle.graph.upsert_entities([EntityRecord(id="x", name="Extra", type="Entity")])
    published = pipeline.complete(first)
    with pytest.raises(ValueError, match="newer generation"):
        pipeline.complete(second)
    assert meta.get(scope) == published
    assert [intent.operation_id for intent in meta.pending_writes(scope)] == ["second"]
    assert len(meta.changes(scope)) == 1
    assert meta.changes(scope)[0].topology_changed
    assert meta.changes(scope)[0].entity_upserts == ("x",)


def test_seeding_is_idempotent_and_retry_does_not_overwrite_completed_chunks(tmp_path):
    raw, scope, meta, pipeline = setup_generation(tmp_path)
    write = pipeline.prepare(scope, operation_id="upload")
    doc = write.bundle.docs.get("doc")
    doc.chunks[0].text = "in-progress chunk"
    write.bundle.docs.save_chunks(doc, doc.chunks)
    resumed = pipeline.prepare(scope, operation_id="upload")
    assert resumed.target_generation == write.target_generation
    assert resumed.bundle.docs.get("doc").chunks[0].text == "in-progress chunk"
    assert len(meta.pending_writes(scope)) == 1


def test_crash_before_seed_completion_replays_source_without_publishing(tmp_path, monkeypatch):
    raw, scope, meta, pipeline = setup_generation(tmp_path)
    from agentic_graphrag.knowledge import generation_pipeline

    original = generation_pipeline.restore_records
    failed = False

    def flaky(bundle, payload):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated interrupted seeding")
        original(bundle, payload)

    monkeypatch.setattr(generation_pipeline, "restore_records", flaky)
    with pytest.raises(OSError):
        pipeline.prepare(scope, operation_id="upload")
    assert meta.pending_writes(scope)[0].stage == "pending"
    assert meta.get(scope).active_generation == "1"
    resumed = pipeline.prepare(scope, operation_id="upload")
    assert resumed.bundle.docs.get("doc").content == "first risk"
    assert meta.pending_writes(scope)[0].stage == "seeded"


def test_delete_change_records_removed_members_and_snapshot_visibility(tmp_path):
    raw, scope, meta, pipeline = setup_generation(tmp_path)
    write = pipeline.prepare(scope, operation_id="delete")
    meta.tombstone(scope, "doc", through_version=1)
    write.bundle.graph.delete_by_doc("doc", "tenant")
    write.bundle.docs.delete("doc", tenant_id="tenant")
    write.bundle.fulltext.delete_by_doc("doc", "tenant")
    write.bundle.vector.delete_by_doc("doc", "tenant")
    published = pipeline.complete(write)
    assert not snapshot_bundle(meta, scope, published.active_generation).docs.list_ids()
    change = meta.changes(scope)[0]
    assert change.relation_deletes == ("r",)
    assert change.source_retractions == ("doc",)
    assert change.document_ids == ("doc",)
    assert not snapshot_bundle(meta, scope, "1").docs.list_ids()


def test_chunk_provenance_is_server_derived_and_repaired_on_resume(tmp_path):
    raw, scope, meta, pipeline = setup_generation(tmp_path)
    bound = scope_bundle(raw, scope)
    doc = bound.docs.get("doc")
    doc.chunks[0].metadata.update(document_version=999, content_sha256="untrusted")
    doc = replace(doc, version=2)
    stamp_chunk_sources(doc)
    assert doc.chunks[0].metadata["document_version"] == 2
    assert doc.chunks[0].metadata["content_sha256"] == doc.content_sha256
    assert len(doc.content_sha256) == 64
    assert doc.chunks[0].kb_id == "contracts"


def test_pending_generation_rebuilds_memory_indexes_after_process_restart(tmp_path):
    scope = KnowledgeScope(tenant_id="tenant", kb_id="contracts")
    docs = FileDocStore(tmp_path / "docs")
    raw = raw_bundle(docs=docs)
    meta = SQLiteKnowledgeStore(tmp_path / "meta.db")
    meta.create(KnowledgeBase(tenant_id="tenant", kb_id="contracts"))
    populate(scope_bundle(raw, scope), scope, content="first risk")
    pipeline = GenerationPipeline(raw, meta)
    write = pipeline.prepare(scope, operation_id="upload")
    populate(write.bundle, scope, content="second risk", version=2)
    mark_ready(write.bundle)
    meta.close()
    restarted = GenerationPipeline(
        raw_bundle(docs=FileDocStore(tmp_path / "docs")),
        SQLiteKnowledgeStore(tmp_path / "meta.db"),
    )
    recovered = restarted.prepare(scope, operation_id="upload")
    doc = recovered.bundle.docs.get("doc")
    assert doc.content == "second risk" and doc.version == 2
    assert not doc.chunks[0].graph_extracted
    assert not doc.metadata["index_complete"]
    assert recovered.bundle.graph.list_relations() == []
    assert recovered.bundle.fulltext.search("second")[0][0].text == "second risk"
    assert recovered.bundle.vector.search([1.0, 0.0])[0][0].text == "second risk"
    with pytest.raises(ValueError, match="finish indexing"):
        restarted.complete(recovered)


def test_change_and_pointer_rollback_together_on_commit_failure(tmp_path, monkeypatch):
    raw, scope, meta, pipeline = setup_generation(tmp_path)
    write = pipeline.prepare(scope, operation_id="upload")
    from agentic_graphrag.stores import knowledge_snapshots

    original = knowledge_snapshots.complete_record

    def failed_commit(db, change):
        raise OSError("journal transaction failed")

    monkeypatch.setattr(knowledge_snapshots, "complete_record", failed_commit)
    with pytest.raises(OSError):
        pipeline.complete(write)
    assert meta.get(scope).active_generation == "1"
    assert meta.pending_writes(scope)
    assert not meta.changes(scope)
    assert meta.get_snapshot(scope, write.target_generation) is not None
    monkeypatch.setattr(knowledge_snapshots, "complete_record", original)
    assert pipeline.complete(write).active_generation == write.target_generation
