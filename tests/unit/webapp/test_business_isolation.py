"""Cookie and service-key principals share the same business isolation boundary."""

import pytest
from fastapi.testclient import TestClient

from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance import audit_events
from agentic_graphrag.governance.audit_events import AuditEvent, AuditEventStore
from agentic_graphrag.governance.audit_store import AuditStore
from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore
from agentic_graphrag.knowledge.review.queue import ReviewQueue
from agentic_graphrag.observability import metrics, trace
from agentic_graphrag.observability.metrics import MetricsRegistry, QueryMetrics
from agentic_graphrag.observability.trace import Tracer
from agentic_graphrag.webapp.business import install_business


@pytest.fixture
def business(web_app, tmp_path, monkeypatch):
    service = QueryService.create_offline()
    service.audit_store = AuditStore()
    service.review_queue = ReviewQueue()
    service.ingest_tasks = IngestTaskStore(tmp_path / "tasks.json")
    install_business(web_app, service)
    monkeypatch.setattr(metrics, "_GLOBAL", MetricsRegistry())
    monkeypatch.setattr(trace, "_TRACER", Tracer())
    monkeypatch.setattr(audit_events, "_STORE", AuditEventStore(tmp_path / "audit.jsonl"))
    yield service
    service.close()


@pytest.mark.parametrize("keyed", [False, True])
def test_admin_observability_is_scoped(browser, business, register_user, monkeypatch, keyed):
    me = register_user(browser)
    tenant = me["tenant_id"]
    headers = {}
    if keyed:
        monkeypatch.setenv("AGR_API_KEYS", f"{tenant}:service-secret:admin")
        headers = {"X-API-Key": "service-secret"}
    for owner in (tenant, "other", ""):
        trace.get_tracer().start(f"trace-{owner}", tenant_id=owner)
        metrics.get_metrics().record(QueryMetrics(owner, tenant_id=owner))
        business.multi_budget.commit(tenant_id=owner, user_id="actor", llm_calls=1)
        audit_events.get_audit_event_store().record(AuditEvent(1, "upload", owner, "u", "", "ok"))
    assert browser.get(f"/v1/traces/trace-{tenant}", headers=headers).status_code == 200
    assert browser.get("/v1/traces/trace-other", headers=headers).status_code == 404
    assert browser.get("/v1/traces/trace-", headers=headers).status_code == 404
    assert browser.get("/v1/metrics", headers=headers).json()["data"]["count"] == 1
    snapshot = browser.get("/v1/budget/snapshot", headers=headers).json()["data"]
    assert set(snapshot["tenants"]) == {tenant}
    events = browser.get("/v1/audit-events", headers=headers).json()["data"]
    assert {event["tenant_id"] for event in events} == {tenant}
    assert browser.get("/v1/audit-events?tenant_id=other", headers=headers).status_code == 404


def test_private_operational_metrics_and_public_health(browser, business, register_user):
    assert browser.get("/metrics-prom").status_code == 401
    health = browser.get("/healthz").json()
    assert health == {"status": "ok", "checks": {"identity": "ok"}}
    me = register_user(browser)
    metrics.get_metrics().record(QueryMetrics("own", tenant_id=me["tenant_id"]))
    metrics.get_metrics().record(QueryMetrics("other", tenant_id="other"))
    response = browser.get("/metrics-prom")
    assert response.status_code == 200
    assert "agr_queries_total 1\n" in response.text


def test_legacy_task_audit_feedback_and_graph_visibility(
    browser, business, register_user, csrf_headers
):
    from agentic_graphrag.api.routes.workspace_tasks import remember_task

    me = register_user(browser)
    assert browser.get("/v1/graph/entities").json()["data"] == []
    remember_task("legacy-without-owner", {"task_id": "legacy-without-owner"})
    assert browser.get("/v1/ingest-tasks/legacy-without-owner").status_code == 404
    own = ReasoningChain(question="own", answer="answer", metadata={"tenant_id": me["tenant_id"]})
    other = ReasoningChain(question="other", answer="secret", metadata={"tenant_id": "other"})
    for chain in (own, other):
        business.audit_store.save(chain)
    assert browser.get(f"/v1/audit/queries/{own.query_id}").status_code == 200
    for query_id in (other.query_id, "missing"):
        assert browser.get(f"/v1/audit/queries/{query_id}").status_code == 404
        response = browser.post(
            "/v1/feedback",
            headers=csrf_headers(browser),
            json={"query_id": query_id, "accurate": False},
        )
        assert response.status_code == 404


def test_same_filename_upload_query_stream_review_and_task_boundaries(
    browser,
    web_app,
    business,
    register_user,
    csrf_headers,
):
    from agentic_graphrag.app.write_path import build_inline_worker

    peer = TestClient(web_app)
    identities = [register_user(browser), register_user(peer, "peer@example.com")]
    clients, people, task_ids = [browser, peer], ["Alice River", "Bruno Lake"], []
    for client, person in zip(clients, people, strict=True):
        response = client.post(
            "/v1/docs",
            files=[
                (
                    "files",
                    ("shared.txt", f"{person} is the CEO of Cedar Lab.".encode(), "text/plain"),
                )
            ],
            headers=csrf_headers(client),
        )
        assert response.status_code == 200
        task_ids.append(response.json()["data"]["id"])
    results = build_inline_worker(business).run_once()
    assert len(results) == 2 and all(result.error is None for result in results)
    for index, client in enumerate(clients):
        other = 1 - index
        assert client.get(f"/v1/ingest-tasks/{task_ids[index]}").status_code == 200
        assert client.get(f"/v1/ingest-tasks/{task_ids[other]}").status_code == 404
        review = client.get("/v1/review-queue").json()["data"]
        assert all(row["tenant_id"] == identities[index]["tenant_id"] for row in review)
        rows = client.get("/v1/graph/entities").json()["data"]
        assert people[index] in str(rows) and people[other] not in str(rows)
        question = {"question": "Who is the CEO of Cedar Lab?", "force_agentic": True}
        answer = client.post("/v1/query", json=question, headers=csrf_headers(client))
        assert answer.status_code == 200
        assert people[index] in answer.json()["data"]["answer"]
        assert people[other] not in answer.text
        stream = client.post("/v1/query/stream", json=question, headers=csrf_headers(client))
        assert "event: answer" in stream.text
        assert people[index] in stream.text and people[other] not in stream.text
    foreign_review = peer.get("/v1/review-queue").json()["data"][0]["id"]
    assert (
        browser.post(
            f"/v1/review-queue/{foreign_review}/decision",
            json={"decision": "reject"},
            headers=csrf_headers(browser),
        ).status_code
        == 404
    )
