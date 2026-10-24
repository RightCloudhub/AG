"""Neo4j entity merge uses validated types and tenant-scoped query parameters."""

from unittest.mock import MagicMock

from agentic_graphrag.stores.neo4j_store import Neo4jGraphStore


def test_neo4j_entity_rewire_uses_one_transaction_and_scopes_tenant() -> None:
    store = Neo4jGraphStore.__new__(Neo4jGraphStore)
    session = MagicMock()
    store._driver = MagicMock()
    store._driver.session.return_value.__enter__.return_value = session
    tx = MagicMock()
    tx.run.side_effect = [[{"kind": "CEO_OF"}], MagicMock(), MagicMock()]
    session.execute_write.side_effect = lambda callback, request: callback(tx, request)
    store.rewire_entity("alias", "canonical", keep_name="Canonical", tenant_id="tenant-a")
    calls = tx.run.call_args_list
    assert len(calls) == 3
    for call in calls:
        assert call.kwargs["tenant_id"] == "tenant-a"
        assert "$tenant_id" in call.args[0]
        assert "tenant-a" not in call.args[0]
    assert "replacement.head_id" in calls[1].args[0]
    assert "replacement.tail_id" in calls[2].args[0]
