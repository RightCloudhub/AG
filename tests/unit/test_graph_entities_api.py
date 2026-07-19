"""GET /v1/graph/entities must list offline seed graph (P5-CAP-01)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.stores.interfaces import EntityRecord
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore


def test_inmemory_list_entities_public_api():
    store = InMemoryGraphStore()
    store.upsert_entities(
        [
            EntityRecord(id="1", name="Apex Holdings", type="Company"),
            EntityRecord(id="2", name="Elena Varga", type="Person"),
        ]
    )
    rows = store.list_entities(limit=10)
    assert len(rows) == 2
    counts = store.counts()
    assert counts["entities"] == 2
    assert counts["nodes"] == 2


def test_graph_entities_endpoint_returns_seed_entities():
    """Drive real offline QueryService + create_app route (shipped path)."""
    svc = QueryService.create_offline()
    # Precondition: offline seed graph must have entities loaded into the store
    store = svc.bundle.graph
    assert isinstance(store, InMemoryGraphStore)
    assert store.counts()["entities"] > 0, "seed triples should load entities"

    app = create_app(query_service=svc)
    client = TestClient(app)
    r = client.get("/v1/graph/entities", params={"limit": 50})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data, list)
    assert len(data) > 0, "expected non-empty entity list from offline seed graph"
    # meta.total should reflect graph size (not 0)
    meta = body.get("meta") or {}
    total = meta.get("total")
    assert total is not None and int(total) >= len(data)
    # Each row has browse fields
    sample = data[0]
    assert sample.get("id")
    assert sample.get("name")
    assert sample.get("type")
    # Known seed entity should appear when limit is high enough
    names = {row["name"] for row in data}
    assert any("Apex" in n or "Elena" in n or "NovaTech" in n for n in names)
    svc.close()


def test_graph_entities_respects_limit():
    svc = QueryService.create_offline()
    app = create_app(query_service=svc)
    client = TestClient(app)
    r = client.get("/v1/graph/entities", params={"limit": 3})
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) <= 3
    assert len(data) >= 1
    svc.close()
