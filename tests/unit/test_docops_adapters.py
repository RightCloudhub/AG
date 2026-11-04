"""Chunk-scoped, revision-fenced index adapters (K-05)."""

import json
from types import SimpleNamespace

from qdrant_client import QdrantClient

from agentic_graphrag.core.ports.stores import (
    ChunkIdentity,
    ChunkKey,
    ChunkRecord,
    ChunkScopedGraph,
    ChunkScopedIndex,
    RelationRecord,
)
from agentic_graphrag.stores.fulltext_shared import SharedFileBM25Store
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore, QdrantVectorStore

KEY = ChunkKey("alpha", "d", "d:0")


def chunk(text="alpha text", revision=1, generation=0, version=1, embedding=(1.0, 0.0)):
    return ChunkRecord(
        "d:0",
        "d",
        text,
        0,
        metadata={"document_version": version},
        tenant_id="alpha",
        embedding=list(embedding),
        revision=revision,
        stages={"vector": {"generation": generation}, "fulltext": {"generation": generation}},
    )


def test_memory_vector_late_writes_and_cleanup_cannot_clobber_newer_revisions():
    store = InMemoryVectorStore()
    assert isinstance(store, ChunkScopedIndex)
    store.upsert([chunk(revision=2, text="new")])
    store.upsert([chunk(revision=1, text="old")])  # late write from an obsolete worker
    assert [c.text for c, _ in store.search([1.0, 0.0], tenant_id="alpha")] == ["new"]
    assert store.delete_chunk(KEY, below=ChunkIdentity(1, 2, 0)) == 0  # late old cleanup
    assert store.delete_chunk(KEY, below=ChunkIdentity(1, 3, 0)) == 1  # disable at revision 3
    store.upsert([chunk(revision=2, text="new")])  # resurrected write below the floor
    assert store.search([1.0, 0.0], tenant_id="alpha") == []
    store.upsert([chunk(revision=4, text="re-enabled")])
    assert store.search([1.0, 0.0], tenant_id="beta") == []
    assert store.delete_chunk(ChunkKey("beta", "d", "d:0")) == 0
    assert store.delete_chunk(KEY) == 1


def test_bm25_identity_fencing_and_shared_snapshot_round_trip(tmp_path):
    memory = BM25FulltextStore()
    memory.index([chunk(revision=3, text="current words")])
    memory.index([chunk(revision=2, text="stale words")])
    assert [c.text for c, _ in memory.search("words", tenant_id="alpha")] == ["current words"]
    assert memory.delete_chunk(KEY, below=ChunkIdentity(1, 3, 0)) == 0
    shared = SharedFileBM25Store(tmp_path / "bm25.json")
    shared.index([chunk(revision=5, generation=2, text="shared words")])
    reloaded = SharedFileBM25Store(tmp_path / "bm25.json")
    hit = reloaded.search("shared", tenant_id="alpha")[0][0]
    assert hit.revision == 5 and hit.identity("fulltext") == ChunkIdentity(1, 5, 2)
    reloaded.index([chunk(revision=4, text="older shared")])
    assert reloaded.search("older", tenant_id="alpha") == []
    assert reloaded.delete_chunk(KEY) == 1
    assert SharedFileBM25Store(tmp_path / "bm25.json").search("shared", tenant_id="alpha") == []


def relation(rid, *sources, tenant="alpha"):
    return RelationRecord(rid, "OWNS", "a", "b", "A", "B", sources=list(sources), tenant_id=tenant)


def test_graph_chunk_support_removal_keeps_facts_supported_elsewhere():
    graph = InMemoryGraphStore()
    assert isinstance(graph, ChunkScopedGraph)
    old = {"doc_id": "d", "chunk_id": "d:0", "doc_version": 1, "revision": 1}
    new = {"doc_id": "d", "chunk_id": "d:0", "doc_version": 1, "revision": 2}
    other = {"doc_id": "e", "chunk_id": "e:0"}
    graph.upsert_relations([relation("only", old), relation("shared", old, other)])
    graph.upsert_relations([relation("fresh", new), relation("foreign", old, tenant="beta")])
    assert graph.delete_chunk_support(KEY, below=ChunkIdentity(1, 2, 0)) == 1
    remaining = {r.id: r.sources for r in graph.list_relations()}
    assert set(remaining) == {"shared", "fresh", "foreign"}
    assert remaining["shared"] == [other] and remaining["fresh"] == [new]
    assert graph.delete_chunk_support(KEY) == 1
    assert {r.id for r in graph.list_relations()} == {"shared", "foreign"}


def local_qdrant():
    store = QdrantVectorStore.__new__(QdrantVectorStore)
    store._client = QdrantClient(":memory:")
    store._collection = "chunks"
    store._routing_enabled = True
    store._dim = None
    return store


def test_qdrant_points_are_revision_scoped_and_cleanup_is_bounded():
    store = local_qdrant()
    store.upsert([chunk(revision=1, text="old")])
    store.upsert([chunk(revision=2, generation=3, text="new")])
    hits = store.search([1.0, 0.0], tenant_id="alpha")
    assert [c.text for c, _ in hits] == ["new"] and hits[0][0].revision == 2
    assert store.delete_chunk(KEY, below=ChunkIdentity(1, 2, 3)) == 1
    assert store.delete_chunk(KEY, below=ChunkIdentity(1, 2, 0)) == 0
    store.upsert([chunk(revision=1, version=2, text="next version")])
    assert store.delete_chunk(KEY, below=ChunkIdentity(1, 9, 9)) == 1
    assert [c.text for c, _ in store.search([1.0, 0.0], tenant_id="alpha")] == ["next version"]
    assert store.delete_chunk(ChunkKey("beta", "d", "d:0")) == 0
    assert store.delete_chunk(KEY) == 1
    store.close()


def test_neo4j_chunk_support_deletion_is_tenant_scoped_and_bounded():
    from agentic_graphrag.stores.neo4j_documents import Neo4jDocumentDeletion

    calls = []
    rows = [
        {"eid": "old", "sources": json.dumps([{"doc_id": "d", "chunk_id": "d:0", "revision": 1}])},
        {"eid": "new", "sources": json.dumps([{"doc_id": "d", "chunk_id": "d:0", "revision": 3}])},
        {
            "eid": "shared",
            "sources": json.dumps([{"doc_id": "d", "chunk_id": "d:0"}, {"chunk_id": "x"}]),
        },
    ]

    class Transaction:
        def run(self, query, **params):
            calls.append((query, params))
            if "RETURN elementId" in query:
                return rows
            return SimpleNamespace(consume=lambda: None)

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute_write(self, fn, *args):
            return fn(Transaction(), *args)

    store = Neo4jDocumentDeletion()
    store._driver = SimpleNamespace(session=Session)
    assert store.delete_chunk_support(KEY, below=ChunkIdentity(0, 2, 0)) == 1
    assert all(params["tenant_id"] == "alpha" for _, params in calls)
    writes = [(q, p) for q, p in calls if "RETURN" not in q]
    assert [p["eid"] for _, p in writes] == ["old", "shared"]
    assert json.loads(writes[1][1]["sources"]) == [{"chunk_id": "x"}]
