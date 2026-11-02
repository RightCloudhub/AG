"""Offline embedding transport and vector publication contracts."""

from types import SimpleNamespace

from qdrant_client import QdrantClient

from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.llm.provider import LLMProvider
from agentic_graphrag.stores.vector_store import QdrantVectorStore


def local_qdrant():
    store = QdrantVectorStore.__new__(QdrantVectorStore)
    store._client = QdrantClient(":memory:")
    store._collection = "chunks"
    store._routing_enabled = True
    store._dim = None
    return store


def test_reindex_publication_is_atomic_across_dimensions_and_tenants():
    store = local_qdrant()
    a = ChunkRecord("a:0", "a", "alpha", 0, tenant_id="alpha", embedding=[1.0, 0.0])
    b = ChunkRecord("b:0", "b", "beta", 0, tenant_id="beta", embedding=[1.0, 0.0])
    store.upsert([a])
    store.upsert([b])
    store.begin_reindex("new-model", tenant_id="alpha")
    new = ChunkRecord("a:0", "a", "alpha", 0, tenant_id="alpha", embedding=[0.0, 1.0, 0.0])
    store.upsert([new])
    assert len(store.search([1.0, 0.0], tenant_id="alpha")) == 1
    store.commit_reindex()
    store.end_reindex()
    assert len(store.search([0.0, 1.0, 0.0], tenant_id="alpha")) == 1
    assert len(store.search([1.0, 0.0], tenant_id="beta")) == 1
    assert store.delete_by_doc("a", "beta") == 0
    assert store.delete_by_doc("a", "alpha") == 1
    assert store.search([0.0, 1.0, 0.0], tenant_id="alpha") == []
    assert len(store.search([1.0, 0.0], tenant_id="beta")) == 1
    store.close()


def test_qdrant_default_tenant_delete_includes_legacy_blank():
    store = local_qdrant()
    for tenant in ("", "default", "alpha"):
        store.upsert(
            [ChunkRecord(f"{tenant}:0", "same", "text", 0, tenant_id=tenant, embedding=[1.0])]
        )
    assert store.delete_by_doc("same", "default") == 2
    assert len(store.search([1.0], tenant_id="alpha")) == 1
    store.close()


def test_embed_many_sends_one_request_and_orders_response(monkeypatch):
    calls = []
    provider = LLMProvider(api_key="fake")

    def post(path, payload, **kwargs):
        calls.append((path, payload))
        return {
            "data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]
        }

    monkeypatch.setattr(provider, "_post", post)
    assert provider.embed_many(["one", "two"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert len(calls) == 1 and calls[0][1]["input"] == ["one", "two"]
    assert provider.embed_many([]) == []


def test_neo4j_document_deletion_preserves_other_provenance():
    import json

    from agentic_graphrag.stores.neo4j_documents import Neo4jDocumentDeletion

    calls = []

    class Transaction:
        def run(self, query, **params):
            calls.append((query, params))
            if query.startswith("MATCH") and "RETURN elementId" in query:
                return [
                    {"eid": "only", "sources": json.dumps([{"doc_id": "a"}])},
                    {"eid": "shared", "sources": json.dumps([{"doc_id": "a"}, {"doc_id": "b"}])},
                    {"eid": "other", "sources": json.dumps([{"doc_id": "b"}])},
                ]
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
    assert store.delete_by_doc("a", "alpha") == 1
    assert all(params["tenant_id"] == "alpha" for _, params in calls)
    assert "DELETE r" in calls[1][0]
    assert json.loads(calls[2][1]["sources"]) == [{"doc_id": "b"}]


def test_query_pins_encoder_and_retries_when_alias_changes_during_embedding():
    from agentic_graphrag.retrieval.vector import VectorRetriever

    store = local_qdrant()
    store.upsert(
        [
            ChunkRecord(
                "a:0",
                "a",
                "alpha",
                0,
                tenant_id="alpha",
                embedding=[1.0, 0.0],
                embedding_model="old",
            )
        ]
    )
    store.begin_reindex("new", tenant_id="alpha")
    store.upsert(
        [
            ChunkRecord(
                "a:0",
                "a",
                "alpha",
                0,
                tenant_id="alpha",
                embedding=[0.0, 1.0, 0.0],
                embedding_model="new",
            )
        ]
    )
    calls = []

    class Embedder:
        def embed_for_model(self, text, model):
            calls.append(model)
            if model == "old":
                store.commit_reindex()  # Simulate cutover while the embedding request is in flight.
                return [1.0, 0.0]
            return [0.0, 1.0, 0.0]

    hits = VectorRetriever(store, Embedder()).search("alpha", tenant_id="alpha")
    assert len(hits) == 1 and calls == ["old", "new"]
    store.end_reindex()
    store.close()


def test_query_embedding_can_use_index_model_without_mutating_provider(monkeypatch):
    provider = LLMProvider(api_key="fake", embedding_model="configured")
    calls = []

    def post(path, payload, **kwargs):
        calls.append(payload)
        return {"data": [{"embedding": [1.0]}]}

    monkeypatch.setattr(provider, "_post", post)
    assert provider.embed_for_model("text", "active-index-model") == [1.0]
    assert calls[0]["model"] == "active-index-model"
    assert provider.embedding_model == "configured"


def test_mixed_tenant_upsert_routes_each_active_model_collection():
    store = local_qdrant()
    store.upsert(
        [
            ChunkRecord("a", "a", "alpha", 0, tenant_id="alpha", embedding=[1.0]),
            ChunkRecord("b", "b", "beta", 0, tenant_id="beta", embedding=[1.0]),
        ]
    )
    store.begin_reindex("new", tenant_id="alpha")
    alpha = ChunkRecord("a", "a", "alpha new", 0, tenant_id="alpha", embedding=[1.0, 0.0])
    store.upsert([alpha])
    store.commit_reindex()
    store.end_reindex()
    beta = ChunkRecord("b", "b", "beta new", 0, tenant_id="beta", embedding=[1.0])
    assert store.upsert([alpha, beta]) == 2
    assert store.search([1.0, 0.0], tenant_id="alpha")[0][0].text == "alpha new"
    assert store.search([1.0], tenant_id="beta")[0][0].text == "beta new"
    store.close()
