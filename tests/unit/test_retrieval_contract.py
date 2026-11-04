"""Public evidence and audit contracts cannot bypass tenant or document visibility."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.routes.workspace_tasks import remember_task
from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.config import AppConfig, Settings
from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.audit_store import AuditStore
from agentic_graphrag.knowledge.chunk_sources import stamp_chunk_sources

A = {"Authorization": "Bearer alpha-key"}
B = {"Authorization": "Bearer beta-key"}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("AGR_REQUIRE_AUTH", "1")
    monkeypatch.setenv("AGR_RATE_LIMIT_QPS", "1000")
    monkeypatch.setenv("AGR_API_KEYS", "alpha:alpha-key:admin,beta:beta-key:admin")
    cfg = AppConfig()
    cfg.paths.cache_dir = str(tmp_path / "cache")
    svc = QueryService.create_offline(cfg=cfg, settings=Settings(), seed_triples=tmp_path / "empty")
    for tenant in ("alpha", "beta"):
        for kb_id in ("one", "two"):
            knowledge_store(svc).create(KnowledgeBase(tenant_id=tenant, kb_id=kb_id))
    with TestClient(create_app(query_service=svc)) as client:
        yield client, svc
    svc.close()


def add_document(svc, *, tenant="alpha", kb_id="one", text="Confidential payment terms."):
    selected = select_service(svc, tenant_id=tenant, kb_id=kb_id)
    doc = DocumentRecord("same-doc", "contract", text, tenant_id=tenant, kb_id=kb_id)
    doc.metadata["index_complete"] = True
    doc.chunks = [ChunkRecord("same-chunk", doc.doc_id, text, 0, fulltext_indexed=True)]
    stamp_chunk_sources(doc)
    selected.bundle.docs.save(doc)
    selected.bundle.fulltext.index(doc.chunks)
    return doc


def test_search_snapshot_original_evidence_and_scope(api):
    client, svc = api
    doc = add_document(svc)
    result = client.post(
        "/v1/search",
        headers=A,
        json={"kb_id": "one", "query": "payment", "doc_ids": [doc.doc_id], "audit_required": True},
    )
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert data["snapshot"]["scope"] == {"tenant_id": "alpha", "kb_id": "one"}
    assert data["audit_status"] == "persisted"
    evidence = data["candidates"][0]
    assert (
        evidence["source_refs"][0]["content_sha256"]
        == hashlib.sha256(doc.content.encode()).hexdigest()
    )
    path = f"/v1/evidence/{evidence['id']}"
    original = client.get(path, headers=A, params={"kb_id": "one", "offset": 2, "limit": 8})
    assert original.json()["data"]["content"] == doc.content[2:10]
    assert client.get(path, headers=A, params={"kb_id": "two"}).status_code == 404
    assert client.get(path, headers=B, params={"kb_id": "one"}).status_code == 404
    audit_path = f"/v1/audit/searches/{data['search_id']}"
    assert client.get(audit_path, headers=A, params={"kb_id": "one"}).is_success
    assert client.get(audit_path, headers=A, params={"kb_id": "two"}).status_code == 404


def test_deleted_evidence_is_withdrawn_from_search_audit(api):
    client, svc = api
    doc = add_document(svc)
    data = client.post("/v1/search", headers=A, json={"kb_id": "one", "query": "payment"}).json()[
        "data"
    ]
    assert client.delete(
        f"/v1/documents/{doc.doc_id}", headers=A, params={"kb_id": "one"}
    ).is_success
    audit = client.get(
        f"/v1/audit/searches/{data['search_id']}", headers=A, params={"kb_id": "one"}
    )
    assert "Confidential" not in audit.text
    assert audit.json()["data"]["candidates"][0]["source_status"] == "deleted"
    evidence = client.get(
        f"/v1/evidence/{data['candidates'][0]['id']}", headers=A, params={"kb_id": "one"}
    )
    assert evidence.json()["data"]["status"] == "deleted"
    assert evidence.json()["data"]["content"] == ""


def test_cached_task_and_feedback_are_knowledge_scoped(api):
    client, svc = api
    remember_task(
        "cached-other-kb",
        {"id": "cached-other-kb", "tenant_id": "alpha", "kb_id": "two", "docs": []},
    )
    assert (
        client.get(
            "/v1/ingest-tasks/cached-other-kb", headers=A, params={"kb_id": "one"}
        ).status_code
        == 404
    )
    svc.audit_store.save(
        ReasoningChain(
            query_id="q-other-kb", question="q", metadata={"tenant_id": "alpha", "kb_id": "two"}
        )
    )
    response = client.post(
        "/v1/feedback",
        headers=A,
        params={"kb_id": "one"},
        json={"query_id": "q-other-kb", "accurate": False},
    )
    assert response.status_code == 404


def test_failed_audit_write_has_no_in_memory_success_receipt(tmp_path):
    directory = tmp_path / "directory"
    directory.mkdir()
    audit = AuditStore()
    audit.path = directory
    with pytest.raises(OSError):
        audit.save(ReasoningChain(query_id="failed", question="q"))
    assert audit.get("failed") is None


def test_optional_search_source_audit_failure_is_explicit_and_required_fails(api, monkeypatch):
    from agentic_graphrag.governance.source_store import SourceStore

    client, svc = api
    add_document(svc)

    def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(SourceStore, "put_many", fail)
    response = client.post("/v1/search", headers=A, json={"query": "payment", "kb_id": "one"})
    assert response.status_code == 200
    assert response.json()["data"]["audit_status"] == "failed"
    assert response.json()["data"]["candidates"][0]["source_status"] == "unavailable"
    required = client.post(
        "/v1/search",
        headers=A,
        json={"query": "payment", "kb_id": "one", "audit_required": True},
    )
    assert required.status_code == 503
    assert required.json()["error"]["code"] == "AUDIT_UNAVAILABLE"


def test_review_identity_is_bound_to_authenticated_actor(api):
    from agentic_graphrag.api.auth import user_id_for_api_key
    from agentic_graphrag.knowledge.review.queue import ReviewType

    client, svc = api
    selected = select_service(svc, tenant_id="alpha", kb_id="one")
    item = selected.review_queue.enqueue(ReviewType.SPOTCHECK, {}, tenant_id="alpha")
    response = client.post(
        f"/v1/review-queue/{item.id}/decision?kb_id=one",
        headers=A,
        json={"decision": "approve", "reviewer": "impersonated-expert"},
    )
    assert response.is_success, response.text
    assert response.json()["data"]["reviewer"] == user_id_for_api_key("alpha-key")
