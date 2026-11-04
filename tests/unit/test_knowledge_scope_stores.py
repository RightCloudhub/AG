"""Knowledge bases isolate the same source and entity identities in every index."""

import hashlib
from dataclasses import replace

import pytest
from pydantic import ValidationError

from agentic_graphrag.core.knowledge import KnowledgeScope
from agentic_graphrag.core.ports.capabilities import EntityLookup, RelationScanner
from agentic_graphrag.core.ports.stores import (
    ChunkRecord,
    DocumentRecord,
    EntityRecord,
    RelationRecord,
)
from agentic_graphrag.stores.doc_store import FileDocStore, InMemoryDocStore
from agentic_graphrag.stores.factory import GraphBackend, StoreBundle, VectorBackend
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.scope_codec import storage_tenant
from agentic_graphrag.stores.scoped_bundle import scope_bundle
from agentic_graphrag.stores.scoped_graph import ScopedGraphStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore


def raw_bundle(*, docs=None):
    return StoreBundle(
        graph=InMemoryGraphStore(),
        vector=InMemoryVectorStore(),
        fulltext=BM25FulltextStore(),
        docs=docs or InMemoryDocStore(),
        graph_backend=GraphBackend.MEMORY,
        vector_backend=VectorBackend.MEMORY,
    )


def populate(bundle, scope, *, content="shared risk", version=1):
    chunk = ChunkRecord(
        chunk_id="chunk",
        doc_id="doc",
        text=content,
        index=0,
        tenant_id=scope.tenant_id,
        embedding=[1.0, 0.0],
        embedded=True,
        fulltext_indexed=True,
        graph_extracted=True,
    )
    doc = DocumentRecord(
        doc_id="doc",
        title=content,
        content=content,
        source_uri="file:///same.txt",
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        version=version,
        chunks=[chunk],
        tenant_id=scope.tenant_id,
    )
    sources = [{"doc_id": "doc", "doc_version": version}]
    bundle.docs.save(doc)
    bundle.vector.upsert([chunk])
    bundle.fulltext.index([chunk])
    bundle.graph.upsert_entities(
        [
            EntityRecord(
                id="a", name="Alice", type="Person", sources=sources, tenant_id=scope.tenant_id
            ),
            EntityRecord(
                id="b", name="Baker", type="Company", sources=sources, tenant_id=scope.tenant_id
            ),
        ]
    )
    bundle.graph.upsert_relations(
        [
            RelationRecord(
                id="r",
                type="WORKS_AT",
                head_id="a",
                tail_id="b",
                head_name="Alice",
                tail_name="Baker",
                sources=sources,
                tenant_id=scope.tenant_id,
            )
        ]
    )
    return doc


@pytest.mark.parametrize("durable", [False, True])
def test_four_stores_isolate_two_tenants_and_two_knowledge_bases(tmp_path, durable):
    raw = raw_bundle(docs=FileDocStore(tmp_path) if durable else InMemoryDocStore())
    scopes = [
        KnowledgeScope(tenant_id=t, kb_id=k)
        for t in ("alpha", "beta")
        for k in ("default", "contracts")
    ]
    for scope in scopes:
        bound = scope_bundle(raw, scope)
        populate(bound, scope, content=f"shared risk {scope.tenant_id} {scope.kb_id}")
    for scope in scopes:
        bound = scope_bundle(raw, scope)
        expected = f"shared risk {scope.tenant_id} {scope.kb_id}"
        doc = bound.docs.get("doc")
        assert doc.content == expected
        assert (doc.tenant_id, doc.kb_id) == (scope.tenant_id, scope.kb_id)
        assert doc.chunks[0].metadata["tenant_id"] == scope.tenant_id
        assert bound.docs.find("file:///same.txt", tenant_id=scope.tenant_id).content == expected
        assert bound.docs.list_refs() == [("doc", scope.tenant_id)]
        assert bound.vector.search([1.0, 0.0])[0][0].text == expected
        assert bound.fulltext.search("shared")[0][0].text == expected
        assert bound.graph.counts()["nodes"] == 2
        assert bound.graph.counts()["relations"] == 1
        assert bound.graph.neighbors("Alice")[0][1].name == "Baker"
        assert len(bound.graph.paths("Alice", "Baker")) == 1
        assert len(bound.graph.list_entities()) == 2
        assert isinstance(bound.graph, EntityLookup)
        assert isinstance(bound.graph, RelationScanner)
        assert bound.graph.get_entity_by_id("a", tenant_id=scope.tenant_id).kb_id == scope.kb_id


def test_default_legacy_records_and_collision_safe_namespaces():
    raw = raw_bundle()
    populate(raw, KnowledgeScope())
    bound = scope_bundle(raw, KnowledgeScope())
    assert bound.docs.get("doc").content == "shared risk"
    assert bound.graph.get_entity_by_name("Alice").id == "a"
    assert storage_tenant(KnowledgeScope(tenant_id="a:b")) == "a:b"
    scoped = KnowledgeScope(tenant_id="a", kb_id="b")
    encoded = storage_tenant(scoped)
    with pytest.raises(ValidationError):
        KnowledgeScope(tenant_id=encoded)
    with pytest.raises(ValidationError):
        KnowledgeScope(kb_id="../../elsewhere")
    assert storage_tenant(scoped, generation="2") != encoded
    with pytest.raises(ValueError):
        storage_tenant(scoped, generation="bad/generation")


def test_bound_writes_cannot_escape_and_returned_records_are_copies():
    raw = raw_bundle()
    scope = KnowledgeScope(tenant_id="a", kb_id="contracts")
    bound = scope_bundle(raw, scope)
    original = populate(bound, scope)
    assert original.kb_id == "default"
    assert original.tenant_id == "a"
    record = bound.graph.get_entity_by_name("Alice")
    record.name = "Changed"
    assert bound.graph.get_entity_by_name("Alice").name == "Alice"
    with pytest.raises(ValueError, match="Tenant"):
        bound.docs.get("doc", tenant_id="b")
    with pytest.raises(ValueError, match="Tenant"):
        bound.docs.save(replace(original, tenant_id="b"))
    with pytest.raises(ValueError, match="knowledge base"):
        bound.docs.save(replace(original, kb_id="foreign"))
    for store in (bound.graph, bound.vector, bound.fulltext):
        with pytest.raises(ValueError, match="clear"):
            store.clear()
    with pytest.raises(ValueError, match="rebind"):
        scope_bundle(bound, KnowledgeScope(tenant_id="b"))
    assert scope_bundle(bound, scope) is bound


def test_deletion_rewiring_and_chunk_checkpoints_only_touch_selected_kb():
    raw = raw_bundle()
    a, b = KnowledgeScope(kb_id="a"), KnowledgeScope(kb_id="b")
    first, second = scope_bundle(raw, a), scope_bundle(raw, b)
    populate(first, a)
    populate(second, b)
    doc = first.docs.get("doc")
    changed = replace(doc.chunks[0], graph_extracted=False)
    first.docs.save_chunks(doc, [changed])
    assert not first.docs.get("doc").chunks[0].graph_extracted
    assert second.docs.get("doc").chunks[0].graph_extracted
    assert isinstance(first.graph, ScopedGraphStore)
    first.graph.rewire_entity("a", "b", keep_name="Baker", tenant_id="default")
    assert first.graph.list_relations()[0].head_id == "b"
    assert second.graph.list_relations()[0].head_id == "a"
    first.graph.delete_entity("a", tenant_id="default")
    assert first.graph.counts()["nodes"] == 1
    assert second.graph.counts()["nodes"] == 2
    assert first.graph.delete_relation("r")
    assert not first.graph.delete_relation("r")
    assert second.graph.list_relations()
    first.vector.delete_by_doc("doc", "default")
    first.fulltext.delete_by_doc("doc", "default")
    first.docs.delete("doc", tenant_id="default")
    assert not first.vector.search([1.0, 0.0])
    assert not first.fulltext.search("shared")
    assert first.docs.get("doc") is None
    assert second.docs.get("doc") is not None
    assert second.vector.search([1.0, 0.0])
    assert second.fulltext.search("shared")


def test_ambiguous_name_requires_id_and_relation_scan_pages_are_scoped():
    raw = raw_bundle()
    scope = KnowledgeScope(kb_id="open")
    bound = scope_bundle(raw, scope)
    populate(bound, scope)
    bound.graph.upsert_entities([EntityRecord(id="c", name="Alice", type="Person")])
    assert bound.graph.get_entity_by_name("Alice") is None
    assert bound.graph.neighbors("Alice") == []
    assert bound.graph.paths("Alice", "Baker") == []
    assert len(bound.graph.entities_named("Alice")) == 2
    assert bound.graph.neighbors_by_id("a", tenant_id="default")[0][1].id == "b"
    assert bound.graph.scan_relations(tenant_id="default", limit=1)[0].id == "r"
    assert not bound.graph.scan_relations(tenant_id="default", offset=1)
    assert bound.graph.list_relations(limit=0) == []


def test_generation_isolation_and_hash_deduplication():
    raw = raw_bundle()
    scope = KnowledgeScope(tenant_id="tenant", kb_id="contracts")
    old = scope_bundle(raw, scope)
    new = scope_bundle(raw, scope, generation="2")
    doc = populate(old, scope, content="old content")
    populate(new, scope, content="new content", version=2)
    assert old.docs.find(doc.content_sha256, tenant_id="tenant", by_hash=True) is not None
    assert new.docs.find(doc.content_sha256, tenant_id="tenant", by_hash=True) is None
    assert old.docs.get("doc").content == "old content"
    assert new.docs.get("doc").content == "new content"
    assert old.graph.index_generation != new.graph.index_generation
    assert old.vector.index_generation != new.vector.index_generation
