"""Published reads use immutable source records, with deletion overriding rollback."""

from dataclasses import replace

import pytest
from test_knowledge_scope_stores import populate, raw_bundle

from agentic_graphrag.core.knowledge import KnowledgeBase, KnowledgeScope
from agentic_graphrag.core.knowledge_versions import KnowledgeSnapshot
from agentic_graphrag.stores.knowledge_store import SQLiteKnowledgeStore
from agentic_graphrag.stores.scoped_bundle import scope_bundle
from agentic_graphrag.stores.snapshot_bundle import capture_snapshot, snapshot_bundle


def test_snapshot_pins_real_doc_graph_vector_and_fulltext_content(tmp_path):
    metadata = SQLiteKnowledgeStore(tmp_path / "metadata.db")
    scope = KnowledgeScope(tenant_id="a", kb_id="contracts")
    metadata.create(KnowledgeBase(tenant_id="a", kb_id="contracts"))
    raw = raw_bundle()
    bound = scope_bundle(raw, scope)
    populate(bound, scope, content="original shared risk")
    manifest = KnowledgeSnapshot(scope=scope, generation="1", graph_version="1", vector_version="1")
    sealed = capture_snapshot(bound, manifest, store=metadata)
    assert sealed.payload_sha256 and sealed.document_selection_fingerprint
    doc = bound.docs.get("doc")
    bound.docs.save(replace(doc, content="changed text", version=2))
    bound.graph.delete_relation("r", tenant_id="a")
    bound.fulltext.delete_by_doc("doc", "a")
    bound.vector.delete_by_doc("doc", "a")
    view = snapshot_bundle(metadata, scope, "1")
    assert view.docs.get("doc").content == "original shared risk"
    assert view.fulltext.search("original")[0][0].text == "original shared risk"
    assert view.vector.search([1.0, 0.0])[0][0].text == "original shared risk"
    assert view.graph.list_relations()[0].id == "r"
    for action in [
        lambda: view.docs.save(doc),
        lambda: view.graph.delete_relation("r"),
        lambda: view.fulltext.index([]),
        lambda: view.vector.upsert([]),
    ]:
        with pytest.raises(ValueError, match="read-only"):
            action()
    metadata.close()
    reopened = SQLiteKnowledgeStore(tmp_path / "metadata.db")
    assert snapshot_bundle(reopened, scope, "1").docs.get("doc").version == 1
    reopened.close()


def test_deletion_tombstones_override_old_snapshot_and_rollback(tmp_path):
    metadata = SQLiteKnowledgeStore(tmp_path / "metadata.db")
    scope = KnowledgeScope()
    metadata.ensure_default("default")
    raw = raw_bundle()
    first, second = scope_bundle(raw, scope), scope_bundle(raw, scope, generation="2")
    populate(first, scope, content="old risk", version=1)
    populate(second, scope, content="new risk", version=2)
    for generation, bundle in [("1", first), ("2", second)]:
        capture_snapshot(
            bundle,
            KnowledgeSnapshot(
                scope=scope,
                generation=generation,
                graph_version=generation,
                vector_version=generation,
            ),
            store=metadata,
        )
    metadata.publish(scope, generation="2", expected_revision=1)
    metadata.tombstone(scope, "doc", through_version=1)
    metadata.publish(scope, generation="1", expected_revision=2)
    old = snapshot_bundle(metadata, scope, "1")
    assert old.docs.list_ids() == []
    assert old.graph.counts()["nodes"] == 0
    assert old.graph.list_relations() == []
    assert not old.fulltext.search("risk")
    assert not old.vector.search([1.0, 0.0])
    assert snapshot_bundle(metadata, scope, "2").docs.get("doc").content == "new risk"


def test_capture_requires_matching_bound_scope(tmp_path):
    metadata = SQLiteKnowledgeStore(tmp_path / "metadata.db")
    manifest = KnowledgeSnapshot(
        scope=KnowledgeScope(), generation="1", graph_version="1", vector_version="1"
    )
    with pytest.raises(ValueError, match="bound"):
        capture_snapshot(raw_bundle(), manifest, store=metadata)
    with pytest.raises(ValueError, match="manifest"):
        capture_snapshot(
            scope_bundle(raw_bundle(), KnowledgeScope(kb_id="different")), manifest, store=metadata
        )
    with pytest.raises(ValueError, match="does not exist"):
        snapshot_bundle(metadata, KnowledgeScope(), "missing")
