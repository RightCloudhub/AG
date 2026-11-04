"""Durable configuration, revision conflicts and recoverable change records."""

import pytest
from pydantic import ValidationError

from agentic_graphrag.core.knowledge import KnowledgeBase, KnowledgeScope, SourceRef, SummaryRecord
from agentic_graphrag.core.knowledge_versions import GraphChangeSet, KnowledgeSnapshot, WriteIntent
from agentic_graphrag.stores.knowledge_store import InMemoryKnowledgeStore, SQLiteKnowledgeStore
from agentic_graphrag.stores.scope_codec import parse_storage_tenant, storage_tenant


def test_configuration_default_creation_and_compare_and_swap(tmp_path):
    first, second = (
        SQLiteKnowledgeStore(tmp_path / "meta.db"),
        SQLiteKnowledgeStore(tmp_path / "meta.db"),
    )
    default = first.ensure_default("tenant")
    assert second.ensure_default("tenant") == default
    created = first.create(KnowledgeBase(tenant_id="tenant", kb_id="contracts", build_mode="open"))
    assert [kb.kb_id for kb in second.list("tenant")] == ["contracts", "default"]
    assert not second.list("another")
    with pytest.raises(ValueError, match="already exists"):
        second.create(created)
    revised = second.update(created.model_copy(update={"name": "New"}), expected_revision=1)
    assert revised.config_revision == 2
    with pytest.raises(ValueError, match="revision conflict"):
        first.update(created, expected_revision=1)
    with pytest.raises(ValueError, match="sealed generation"):
        first.update(revised.model_copy(update={"build_mode": "schema"}), expected_revision=2)
    assert first.get(KnowledgeScope(tenant_id="tenant", kb_id="contracts")) == revised
    assert first.list("tenant", limit=1, offset=1) == [default]
    first.close()
    second.close()


def test_configuration_and_source_validation():
    with pytest.raises(ValidationError):
        KnowledgeBase(build_mode="open", schema_id="domain")
    with pytest.raises(ValidationError):
        SourceRef(
            scope=KnowledgeScope(), doc_id="d", doc_version=0, chunk_id="c", content_sha256="a"
        )
    with pytest.raises(ValidationError):
        SourceRef(
            scope=KnowledgeScope(),
            doc_id="d",
            doc_version=1,
            chunk_id="c",
            content_sha256="a" * 64,
            span_start=4,
            span_end=3,
        )
    for scope in [KnowledgeScope(), KnowledgeScope(tenant_id="a:b", kb_id="c")]:
        for generation in ("1", "shadow-2"):
            assert parse_storage_tenant(storage_tenant(scope, generation=generation)) == (
                scope,
                generation,
            )
    assert parse_storage_tenant("") == (KnowledgeScope(), "1")
    with pytest.raises(ValueError):
        parse_storage_tenant('\x1fagkb:["a","default","1"]')


def test_persistent_outbox_restart_idempotence_and_ordering(tmp_path):
    path = tmp_path / "meta.db"
    scope = KnowledgeScope(tenant_id="t", kb_id="k")
    store = SQLiteKnowledgeStore(path)
    intent = WriteIntent(
        operation_id="one",
        scope=scope,
        source_generation="1",
        target_generation="2",
        document_ids=("doc",),
    )
    assert store.begin_write(intent) == intent
    store.close()
    reopened = SQLiteKnowledgeStore(path)
    assert reopened.pending_writes(scope) == [intent]
    assert reopened.pending_writes(KnowledgeScope()) == []
    with pytest.raises(ValueError, match="another write"):
        reopened.begin_write(intent.model_copy(update={"target_generation": "3"}))
    change = GraphChangeSet(
        operation_id="one",
        scope=scope,
        source_generation="1",
        target_generation="2",
        document_ids=("doc",),
        relation_upserts=("r",),
    )
    completed = reopened.complete_write(change)
    assert completed.sequence > 0
    assert reopened.complete_write(change) == completed
    assert not reopened.pending_writes(scope)
    assert reopened.begin_write(intent).status == "completed"
    assert reopened.changes(scope) == [completed]
    assert not reopened.changes(scope, after_sequence=completed.sequence)
    with pytest.raises(ValueError, match="cannot change"):
        reopened.complete_write(change.model_copy(update={"relation_upserts": ("other",)}))
    with pytest.raises(ValueError, match="persisted intent"):
        reopened.complete_write(change.model_copy(update={"operation_id": "missing"}))
    reopened.close()


def test_snapshot_is_sealed_and_tombstones_are_monotonic():
    store = InMemoryKnowledgeStore()
    scope = KnowledgeScope(kb_id="contracts")
    store.create(KnowledgeBase(kb_id="contracts"))
    manifest = KnowledgeSnapshot(scope=scope, generation="2", graph_version="2", vector_version="2")
    sealed = store.save_snapshot(manifest, '{"records": []}')
    assert sealed.payload_sha256
    assert store.save_snapshot(manifest, '{ "records" : [] }') == sealed
    with pytest.raises(ValueError, match="overwritten"):
        store.save_snapshot(manifest, '{"records": [1]}')
    with pytest.raises(ValueError, match="manifest hash"):
        store.save_snapshot(manifest.model_copy(update={"payload_sha256": "a" * 64}), "{}")
    with pytest.raises(ValueError, match="sealed"):
        store.publish(scope, generation="missing", expected_revision=1)
    kb = store.publish(scope, generation="2", expected_revision=1)
    assert (kb.active_generation, kb.config_revision) == ("2", 2)
    with pytest.raises(ValueError, match="revision conflict"):
        store.publish(scope, generation="2", expected_revision=1)
    store.tombstone(scope, "doc", through_version=3)
    store.tombstone(scope, "doc", through_version=1)
    assert store.is_tombstoned(scope, "doc", version=3)
    assert not store.is_tombstoned(scope, "doc", version=4)
    assert not store.is_tombstoned(KnowledgeScope(), "doc", version=1)
    store.close()


def test_summary_input_fingerprints_protect_against_stale_worker_outputs():
    store = InMemoryKnowledgeStore()
    scope = KnowledgeScope(kb_id="contracts")
    summary = SummaryRecord(
        summary_id="s",
        scope=scope,
        generation="1",
        kind="document",
        input_fingerprint="one",
        model_revision="m",
        prompt_version="p",
    )
    store.save_summary(summary)
    ready = summary.model_copy(update={"status": "ready"})
    store.save_summary(ready)
    assert store.summaries(scope, generation="1") == [ready]
    assert not store.summaries(KnowledgeScope(), generation="1")
    with pytest.raises(ValueError, match="fingerprint"):
        store.save_summary(summary.model_copy(update={"input_fingerprint": "other"}))
    store.save_summary(summary.model_copy(update={"generation": "2"}))
    assert store.summaries(scope, generation="1") == [ready]
