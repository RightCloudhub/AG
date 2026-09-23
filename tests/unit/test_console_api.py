"""Workspace identity, task listing, and role-scoped API contracts."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.api.service_query import _budget_api_error
from agentic_graphrag.knowledge.ingest_tasks import IngestStatus, IngestTaskStore
from agentic_graphrag.knowledge.review.queue import ReviewQueue, ReviewType
from agentic_graphrag.llm.budget import BudgetExceeded, BudgetTracker
from agentic_graphrag.observability import audit_events
from agentic_graphrag.observability.audit_events import AuditEventStore
from agentic_graphrag.stores.doc_store import InMemoryDocStore


def test_identity_capabilities_follow_authenticated_role(monkeypatch):
    monkeypatch.setenv("AGR_REQUIRE_AUTH", "1")
    monkeypatch.setenv(
        "AGR_API_KEYS",
        "tenant-a:reader-key:reader,tenant-a:operator-key:operator,tenant-a:admin-key:admin",
    )
    service = QueryService.create_offline()
    client = TestClient(create_app(query_service=service))

    assert client.get("/v1/me").status_code == 401
    response = client.get("/v1/me", headers={"Authorization": "Bearer operator-key"})
    assert response.status_code == 200
    identity = response.json()["data"]
    assert identity["tenant_id"] == "tenant-a"
    assert identity["role"] == "operator"
    assert identity["authenticated"] is True
    assert identity["capabilities"] == {
        "chat": True,
        "graph": True,
        "knowledge": True,
        "review": True,
        "ops": False,
    }
    reader = {"Authorization": "Bearer reader-key"}
    assert client.get("/v1/graph/entities", headers=reader).status_code == 200
    assert client.get("/v1/ingest-tasks", headers=reader).status_code == 403
    assert client.get("/v1/review-queue", headers=reader).status_code == 403
    service.close()


def test_ingest_task_listing_is_paginated_and_tenant_scoped(monkeypatch):
    monkeypatch.delenv("AGR_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("AGR_API_KEYS", raising=False)
    service = QueryService.create_offline()
    service.bundle.docs = InMemoryDocStore()
    service.ingest_tasks = IngestTaskStore()
    service.ingest_tasks.create("default", [{"doc_id": "visible.txt"}], task_id="visible")
    service.ingest_tasks.create("other-tenant", [{"doc_id": "private.txt"}], task_id="private")
    client = TestClient(create_app(query_service=service))

    response = client.get("/v1/ingest-tasks", params={"limit": 1, "offset": 0})
    assert response.status_code == 200
    assert [task["id"] for task in response.json()["data"]] == ["visible"]
    assert response.json()["meta"]["total"] == 1
    assert client.get("/v1/ingest-tasks", params={"limit": 0}).status_code == 422
    service.close()


def test_review_queue_blank_status_means_all(monkeypatch):
    monkeypatch.delenv("AGR_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("AGR_API_KEYS", raising=False)
    service = QueryService.create_offline()
    service.review_queue = ReviewQueue()
    service.review_queue.enqueue(ReviewType.SPOTCHECK, {"task_id": "one"}, tenant_id="default")
    decided = service.review_queue.enqueue(
        ReviewType.FEEDBACK,
        {"query_id": "two"},
        tenant_id="default",
    )
    service.review_queue.decide(decided.id, "approve", tenant_id="default")
    client = TestClient(create_app(query_service=service))

    response = client.get("/v1/review-queue", params={"status": ""})
    assert response.status_code == 200
    assert {item["status"] for item in response.json()["data"]} == {"pending", "approved"}
    assert response.json()["meta"]["total"] == 2
    assert response.json()["meta"]["extra"]["pending_count"] == 1
    service.close()


def test_review_queue_pagination_reports_tenant_total(monkeypatch):
    monkeypatch.delenv("AGR_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("AGR_API_KEYS", raising=False)
    service = QueryService.create_offline()
    service.review_queue = ReviewQueue()
    for item_number in range(3):
        service.review_queue.enqueue(
            ReviewType.SPOTCHECK,
            {"item_number": item_number},
            tenant_id="default",
        )
    service.review_queue.enqueue(ReviewType.SPOTCHECK, {}, tenant_id="other-tenant")
    client = TestClient(create_app(query_service=service))

    response = client.get("/v1/review-queue", params={"limit": 2, "offset": 1})
    assert response.status_code == 200
    assert len(response.json()["data"]) == 2
    assert response.json()["meta"]["total"] == 3
    assert response.json()["meta"]["extra"]["pending_count"] == 3
    assert client.get("/v1/review-queue", params={"limit": 0}).status_code == 422
    service.close()


def test_budget_exceeded_is_recorded_as_security_event(monkeypatch, tmp_path):
    event_store = AuditEventStore(tmp_path / "events.jsonl")
    monkeypatch.setattr(audit_events, "_STORE", event_store)

    error = _budget_api_error(BudgetExceeded("limit reached", BudgetTracker()))

    assert error.code == "BUDGET_EXCEEDED"
    events = event_store.list_events(action="budget_exceeded")
    assert len(events) == 1
    assert events[0].outcome == "denied"


def test_upload_task_and_review_workflow_is_tenant_scoped(monkeypatch):
    monkeypatch.setenv("AGR_REQUIRE_AUTH", "1")
    monkeypatch.setenv(
        "AGR_API_KEYS",
        "tenant-a:operator-key:operator,tenant-b:reader-key:reader,"
        "tenant-b:operator-b-key:operator",
    )
    service = QueryService.create_offline()
    service.bundle.docs = InMemoryDocStore()
    service.ingest_tasks = IngestTaskStore()
    service.review_queue = ReviewQueue()
    client = TestClient(create_app(query_service=service))
    operator = {"Authorization": "Bearer operator-key"}
    reader = {"Authorization": "Bearer reader-key"}
    other_operator = {"Authorization": "Bearer operator-b-key"}

    uploaded = client.post(
        "/v1/docs",
        files={"files": ("sample.md", "# 资料".encode(), "text/markdown")},
        headers=operator,
    )
    assert uploaded.status_code == 200
    task = uploaded.json()["data"]
    task_id = task["id"]
    assert task["status"] == "queued"
    assert task["docs"][0]["bytes"] == str(len("# 资料".encode()))

    listed = client.get("/v1/ingest-tasks", headers=operator)
    assert listed.status_code == 200
    assert listed.json()["data"][0]["id"] == task_id
    assert client.get(f"/v1/ingest-tasks/{task_id}", headers=other_operator).status_code == 404
    assert client.get("/v1/ingest-tasks", headers=other_operator).json()["data"] == []
    assert client.get("/v1/ingest-tasks", headers=reader).status_code == 403
    assert client.get("/v1/review-queue", headers=reader).status_code == 403
    assert client.post(
        "/v1/docs",
        files={"files": ("denied.txt", b"no", "text/plain")},
        headers=reader,
    ).status_code == 403

    service.ingest_tasks.transition(task_id, IngestStatus.EXTRACTING)
    current = client.get(f"/v1/ingest-tasks/{task_id}", headers=operator).json()["data"]
    assert current["status"] == "extracting"
    service.ingest_tasks.transition(task_id, IngestStatus.DONE, message="indexed")
    current = client.get(f"/v1/ingest-tasks/{task_id}", headers=operator).json()["data"]
    assert current["status"] == "done"

    reviews = client.get("/v1/review-queue", headers=operator).json()["data"]
    spotcheck = next(item for item in reviews if item["payload"].get("task_id") == task_id)
    decided = client.post(
        f"/v1/review-queue/{spotcheck['id']}/decision",
        json={"decision": "approve", "reviewer": "operator-a", "note": "verified"},
        headers=operator,
    )
    assert decided.status_code == 200
    assert decided.json()["data"]["status"] == "approved"
    assert client.get("/v1/review-queue", headers=other_operator).json()["data"] == []
    service.close()
