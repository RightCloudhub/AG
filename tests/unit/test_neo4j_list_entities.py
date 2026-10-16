"""Neo4j store must implement ``list_entities`` (runtime resolution / browse / vocab)."""

from __future__ import annotations

from unittest.mock import MagicMock

from agentic_graphrag.stores.interfaces import GraphStore
from agentic_graphrag.stores.neo4j_store import Neo4jGraphStore


class _Node(dict):
    def __init__(self, label: str, **props) -> None:
        super().__init__(props)
        self.labels = {label}


def _store_with_rows(rows):
    store = Neo4jGraphStore.__new__(Neo4jGraphStore)
    session = MagicMock()
    session.run.return_value = rows
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    store._driver = driver
    return store, session


def test_neo4j_list_entities_maps_nodes_and_passes_tenant() -> None:
    rows = [{"e": _Node("Company", id="e1", name="NovaTech", tenant_id="t1")}]
    store, session = _store_with_rows(rows)
    out = store.list_entities(limit=5, offset=2, tenant_id="t1")
    assert [(e.name, e.type, e.tenant_id) for e in out] == [("NovaTech", "Company", "t1")]
    _, kwargs = session.run.call_args
    assert kwargs == {"tenant_id": "t1", "offset": 2, "limit": 5}


def test_neo4j_list_entities_zero_limit_short_circuits() -> None:
    store, session = _store_with_rows([])
    assert store.list_entities(limit=0) == []
    session.run.assert_not_called()


def test_neo4j_store_satisfies_graph_protocol() -> None:
    store = Neo4jGraphStore.__new__(Neo4jGraphStore)
    assert isinstance(store, GraphStore)
