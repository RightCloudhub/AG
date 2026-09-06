"""Console support endpoints (P5-UI-02 M0): /v1/me + /v1/ingest-tasks."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore


@pytest.fixture()
def offline_svc():
    svc = QueryService.create_offline()
    yield svc
    svc.close()


def _client(svc: QueryService) -> TestClient:
    return TestClient(create_app(query_service=svc))


def test_me_anonymous_reports_reader(offline_svc):
    client = _client(offline_svc)
    r = client.get("/v1/me")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"] == {
        "tenant_id": "default",
        "user_id": "anonymous",
        "role": "reader",
    }


def test_me_keyed_principal_carries_key_role(offline_svc, monkeypatch):
    monkeypatch.setenv("AGR_API_KEYS", "acme:op-secret:operator,acme:rd-secret:reader")
    client = _client(offline_svc)

    r = client.get("/v1/me", headers={"Authorization": "Bearer op-secret"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["role"] == "operator"
    assert data["tenant_id"] == "acme"
    assert data["user_id"].startswith("key:")

    r = client.get("/v1/me", headers={"X-API-Key": "rd-secret"})
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "reader"


def test_ingest_tasks_forbidden_without_operator_role(offline_svc, monkeypatch):
    monkeypatch.setenv("AGR_API_KEYS", "acme:op-secret:operator,acme:rd-secret:reader")
    monkeypatch.setenv("AGR_REQUIRE_AUTH", "1")
    client = _client(offline_svc)

    assert client.get("/v1/ingest-tasks").status_code == 401
    r = client.get("/v1/ingest-tasks", headers={"Authorization": "Bearer rd-secret"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"

    r = client.get("/v1/ingest-tasks", headers={"Authorization": "Bearer op-secret"})
    assert r.status_code == 200


def test_ingest_tasks_pagination_and_tenant_scope(offline_svc, tmp_path, monkeypatch):
    monkeypatch.setenv("AGR_API_KEYS", "acme:op-secret:operator")
    store = IngestTaskStore(tmp_path / "ingest_tasks.jsonl")
    offline_svc.ingest_tasks = store
    store.create(tenant_id="acme", docs=[{"name": "a.md", "content": "x"}])
    store.create(tenant_id="other", docs=[{"name": "b.md", "content": "y"}])
    legacy = store.create(tenant_id="", docs=[{"name": "c.md", "content": "z"}])
    store.create(tenant_id="acme", docs=[{"name": "d.md", "content": "w"}])

    client = _client(offline_svc)
    headers = {"Authorization": "Bearer op-secret"}

    r = client.get("/v1/ingest-tasks", params={"limit": 1, "offset": 0}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    # Newest first; acme's own rows + the legacy row are visible, other's are not.
    assert body["meta"]["total"] == 3
    assert body["meta"]["limit"] == 1
    assert body["meta"]["page"] == 1
    assert [d["tenant_id"] for d in body["data"]] == ["acme"]

    r2 = client.get("/v1/ingest-tasks", params={"limit": 2, "offset": 1}, headers=headers)
    assert r2.status_code == 200
    ids = [d["id"] for d in r2.json()["data"]]
    assert len(ids) == 2
    assert all(tenant != "other" for tenant in [d["tenant_id"] for d in r2.json()["data"]])

    r3 = client.get("/v1/ingest-tasks", headers=headers)
    assert legacy.id in [d["id"] for d in r3.json()["data"]]


def test_ingest_tasks_503_when_store_missing(offline_svc, monkeypatch):
    monkeypatch.setenv("AGR_API_KEYS", "acme:op-secret:operator")
    offline_svc.ingest_tasks = None
    client = _client(offline_svc)
    r = client.get("/v1/ingest-tasks", headers={"Authorization": "Bearer op-secret"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
