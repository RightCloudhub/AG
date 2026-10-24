"""Vector adapter contract tests without a running Qdrant service."""

from types import SimpleNamespace

from agentic_graphrag.stores.vector_store import QdrantVectorStore


def test_qdrant_search_uses_query_points_and_preserves_tenant_filter() -> None:
    calls = []

    class Client:
        def query_points(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                points=[
                    SimpleNamespace(
                        id="point",
                        score=0.75,
                        payload={"chunk_id": "chunk", "doc_id": "doc", "text": "content"},
                    )
                ]
            )

    store = QdrantVectorStore.__new__(QdrantVectorStore)
    store._client = Client()
    store._collection = "test"
    result = store.search([1.0, 0.0], top_k=3, tenant_id="tenant-a")

    assert result[0][0].chunk_id == "chunk"
    assert result[0][1] == 0.75
    assert calls[0]["query"] == [1.0, 0.0]
    assert calls[0]["limit"] == 3
    assert calls[0]["query_filter"].must[0].match.value == "tenant-a"
