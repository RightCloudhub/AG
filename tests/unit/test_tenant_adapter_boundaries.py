"""Offline contracts for the live adapter isolation predicates and entity mutations."""

from unittest.mock import MagicMock

from agentic_graphrag.core.ports.stores import ChunkRecord, EntityRecord, RelationRecord
from agentic_graphrag.knowledge.review.executor import ReviewExecutor, ReviewExecutorDeps
from agentic_graphrag.knowledge.review.queue import ReviewItem, ReviewStatus, ReviewType
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.neo4j_store import Neo4jGraphStore
from agentic_graphrag.stores.vector_store import QdrantVectorStore


def test_qdrant_same_chunk_id_uses_distinct_points_per_tenant():
    store = QdrantVectorStore.__new__(QdrantVectorStore)
    store._client, store._collection, store._dim = MagicMock(), "test", 1
    store.upsert(
        [
            ChunkRecord("same", "doc", tenant, 0, embedding=[1.0], tenant_id=tenant)
            for tenant in ("alpha", "beta")
        ]
    )
    points = store._client.upsert.call_args.kwargs["points"]
    assert points[0].id != points[1].id


def test_neo4j_merge_identity_includes_tenant_for_nodes_and_edges():
    store = Neo4jGraphStore.__new__(Neo4jGraphStore)
    store._driver = MagicMock()
    session = store._driver.session.return_value.__enter__.return_value
    store.upsert_entities([EntityRecord("same", "Name", "Entity", tenant_id="alpha")])
    query = session.run.call_args.args[0]
    assert "{id: $id, tenant_id: $tenant_id}" in query
    store.upsert_relations([RelationRecord("same", "KNOWS", "head", "tail", tenant_id="alpha")])
    query = session.run.call_args.args[0]
    assert "{id: $head_id, tenant_id: $tenant_id}" in query
    assert "{id: $tail_id, tenant_id: $tenant_id}" in query
    assert "{id: $id, tenant_id: $tenant_id}" in query


def test_review_resolution_looks_up_names_only_in_its_tenant():
    graph = InMemoryGraphStore()
    for tenant in ("alpha", "beta"):
        graph.upsert_entities(
            [
                EntityRecord("keep", "Keep", "Entity", tenant_id=tenant),
                EntityRecord("drop", "Drop", "Entity", tenant_id=tenant),
            ]
        )
    item = ReviewItem(
        "review",
        ReviewType.RESOLUTION,
        {"keep": "Keep", "drop": "Drop"},
        status=ReviewStatus.APPROVED,
        tenant_id="beta",
    )
    outcome = ReviewExecutor(ReviewExecutorDeps(graph)).execute(item)
    assert outcome["applied"] is True
    assert {e.id for e in graph.list_entities(tenant_id="alpha")} == {"keep", "drop"}
    assert {e.id for e in graph.list_entities(tenant_id="beta")} == {"keep"}
