"""P2-ARCH-02 — repository protocols + factory."""

from __future__ import annotations

from agentic_graphrag.llm.interfaces import LLMClient
from agentic_graphrag.llm.provider import MockLLMProvider
from agentic_graphrag.stores.doc_store import FileDocStore, InMemoryDocStore
from agentic_graphrag.stores.factory import (
    GraphBackend,
    VectorBackend,
    create_doc_store,
    create_fulltext_store,
    create_graph_store,
    create_offline_bundle,
    create_vector_store,
)
from agentic_graphrag.stores.interfaces import (
    ChunkRecord,
    DocStore,
    DocumentRecord,
    FulltextStore,
    GraphStore,
    VectorStore,
)
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore


def test_memory_graph_satisfies_protocol() -> None:
    store = InMemoryGraphStore()
    assert isinstance(store, GraphStore)
    assert store.counts()["nodes"] == 0
    store.close()


def test_memory_vector_satisfies_protocol() -> None:
    store = InMemoryVectorStore()
    assert isinstance(store, VectorStore)
    store.ensure_collection(4)
    n = store.upsert(
        [
            ChunkRecord(
                chunk_id="c1",
                doc_id="d1",
                text="hello world",
                index=0,
                embedding=[1.0, 0.0, 0.0, 0.0],
            )
        ]
    )
    assert n == 1
    hits = store.search([1.0, 0.0, 0.0, 0.0], top_k=1)
    assert hits and hits[0][0].chunk_id == "c1"
    store.clear()
    store.close()


def test_doc_stores_satisfy_protocol(tmp_path) -> None:
    mem: DocStore = InMemoryDocStore()
    file_store: DocStore = FileDocStore(tmp_path)
    doc = DocumentRecord(doc_id="d1", title="T", content="body", metadata={"k": "v"})
    for store in (mem, file_store):
        assert isinstance(store, DocStore)
        store.save(doc)
        got = store.get("d1")
        assert got is not None
        assert got.content == "body"
        assert "d1" in store.list_ids()


def test_fulltext_protocol() -> None:
    ft = create_fulltext_store()
    assert isinstance(ft, FulltextStore)
    ft.index([ChunkRecord(chunk_id="c1", doc_id="d1", text="NovaTech Industries server", index=0)])
    hits = ft.search("NovaTech", top_k=5)
    assert hits


def test_factory_memory_backends() -> None:
    g, gb = create_graph_store(GraphBackend.MEMORY)
    v, vb = create_vector_store(VectorBackend.MEMORY)
    assert gb is GraphBackend.MEMORY
    assert vb is VectorBackend.MEMORY
    assert isinstance(g, GraphStore)
    assert isinstance(v, VectorStore)
    docs = create_doc_store(memory=True)
    assert isinstance(docs, InMemoryDocStore)


def test_offline_bundle_close() -> None:
    bundle = create_offline_bundle(load_bm25=False, load_embeddings=False)
    assert isinstance(bundle.graph, GraphStore)
    assert isinstance(bundle.vector, VectorStore)
    bundle.close()


def test_mock_llm_satisfies_client_protocol() -> None:
    llm = MockLLMProvider()
    assert isinstance(llm, LLMClient)
    vec = llm.embed("test")
    assert len(vec) == 8
    assert llm.embed_many(["a", "b"])
