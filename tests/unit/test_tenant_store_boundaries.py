"""Regressions for legacy visibility and tenant-local storage identifiers."""

import json

import pytest

from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord, EntityRecord
from agentic_graphrag.knowledge.review.queue import ReviewDecision, ReviewQueue
from agentic_graphrag.stores.doc_store import FileDocStore, InMemoryDocStore
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore


@pytest.mark.parametrize("disk", [False, True])
def test_same_document_id_survives_in_two_tenants(tmp_path, disk):
    store = FileDocStore(tmp_path) if disk else InMemoryDocStore()
    for tenant in ("alpha", "beta"):
        store.save(DocumentRecord("shared", "Title", tenant, tenant_id=tenant))
    assert store.get("shared", tenant_id="alpha").content == "alpha"
    assert store.get("shared", tenant_id="beta").content == "beta"
    assert store.get("shared", tenant_id="missing") is None
    assert store.list_ids(tenant_id="alpha") == ["shared"]
    assert store.get("shared") is None  # Ambiguous unscoped lookup must not guess.


def test_document_paths_do_not_alias_and_legacy_is_default_only(tmp_path):
    store = FileDocStore(tmp_path)
    for doc_id in ("a/b", "a_b", "../outside"):
        store.save(DocumentRecord(doc_id, "Title", doc_id, tenant_id="alpha"))
    for doc_id in ("a/b", "a_b", "../outside"):
        assert store.get(doc_id, tenant_id="alpha").content == doc_id
    (tmp_path / "legacy.json").write_text(json.dumps({"doc_id": "legacy", "content": "old"}))
    assert store.get("legacy", tenant_id="default").content == "old"
    assert store.get("legacy", tenant_id="alpha") is None
    assert store.list_ids(tenant_id="default") == ["legacy"]


@pytest.mark.parametrize("kind", ["fulltext", "vector"])
def test_chunk_ids_and_legacy_visibility_are_tenant_scoped(kind):
    store = BM25FulltextStore() if kind == "fulltext" else InMemoryVectorStore()
    chunks = [
        ChunkRecord("shared", "doc", f"secret {tenant}", 0, embedding=[1.0], tenant_id=tenant)
        for tenant in ("alpha", "beta", "")
    ]
    if kind == "fulltext":
        store.index(chunks)
        query = "secret"
    else:
        store.upsert(chunks)
        query = [1.0]
    for tenant in ("alpha", "beta"):
        hits = store.search(query, tenant_id=tenant)
        assert [chunk.tenant_id for chunk, _ in hits] == [tenant]
    assert [chunk.tenant_id for chunk, _ in store.search(query, tenant_id="default")] == [""]
    assert len(store.search(query)) == 3


def test_other_tenant_corpus_cannot_change_bm25_scores():
    store = BM25FulltextStore()
    store.index([ChunkRecord("one", "doc", "target evidence", 0, tenant_id="alpha")])
    before = store.search("target", tenant_id="alpha")
    store.index([ChunkRecord("two", "doc", "target " * 200, 0, tenant_id="beta")])
    assert store.search("target", tenant_id="alpha") == before


def test_legacy_graph_entities_are_not_shared_with_new_tenants():
    graph = InMemoryGraphStore()
    graph.upsert_entities([EntityRecord("old", "Legacy", "Entity")])
    assert graph.list_entities(tenant_id="alpha") == []
    assert len(graph.list_entities(tenant_id="default")) == 1
    assert len(graph.list_entities()) == 1


def test_legacy_review_item_cannot_be_decided_by_another_tenant():
    queue = ReviewQueue()
    item = queue.enqueue("spotcheck", {})
    with pytest.raises(KeyError):
        queue.decide(item.id, ReviewDecision.REJECT, tenant_id="alpha")
    assert queue.list(tenant_id="alpha") == []
    assert queue.list(tenant_id="default") == [item]
