"""Document/chunk operations API contracts (K-01…K-04), offline with the inline worker."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.app.write_path import build_inline_worker
from agentic_graphrag.governance import audit_events
from agentic_graphrag.governance.audit_events import AuditEventStore

A = {"Authorization": "Bearer alpha-key"}
B = {"Authorization": "Bearer beta-key"}
R = {"Authorization": "Bearer reader-key"}
BODY = b"# Guide\n\nAcme makes widgets.\n\n# Notes\n\nBeta makes gadgets.\n"
SUMMARY_KEYS = {
    "doc_id",
    "title",
    "source",
    "version",
    "created_at",
    "updated_at",
    "parse",
    "chunks",
    "indexes",
    "pending_operation",
    "source_artifact",
}
CHUNK_KEYS = {
    "chunk_id",
    "doc_id",
    "ordinal",
    "revision",
    "enabled",
    "breadcrumb",
    "token_count",
    "text_preview",
    "text_sha256",
    "stages",
}


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("AGR_REQUIRE_AUTH", "1")
    monkeypatch.setenv(
        "AGR_API_KEYS", "alpha:alpha-key:operator,beta:beta-key:operator,alpha:reader-key:reader"
    )
    monkeypatch.setenv("AGR_RATE_LIMIT_QPS", "1000")
    monkeypatch.setenv("AGR_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(audit_events, "_STORE", AuditEventStore(tmp_path / "audit.jsonl"))
    svc = QueryService.create_offline()
    with TestClient(create_app(query_service=svc)) as client:
        yield client, svc
    svc.close()


def upload(client, body=BODY, name="guide.md", headers=A):
    response = client.post("/v1/docs", headers=headers, files={"files": (name, body)})
    assert response.status_code == 200
    return response.json()["data"]["docs"][0]


def indexed_doc(client, svc):
    did = upload(client)["doc_id"]
    build_inline_worker(svc).run_once()
    return did


def chunks(client, did, headers=A):
    return client.get(f"/v1/documents/{did}/chunks", headers=headers)


def patch(client, did, cid, body, key="k1", headers=A):
    extra = {"Idempotency-Key": key} if key else {}
    return client.patch(
        f"/v1/documents/{did}/chunks/{cid}", headers={**headers, **extra}, json=body
    )


def test_document_list_and_detail_are_typed_bounded_and_tenant_scoped(api):
    client, svc = api
    did = indexed_doc(client, svc)
    upload(client, b"other tenant", "x.txt", headers=B)
    listing = client.get("/v1/documents?limit=10", headers=A).json()["data"]
    assert listing["total"] == 1 and listing["limit"] == 10 and listing["offset"] == 0
    assert listing["filters"] == {"query": "", "status": ""}
    doc = listing["items"][0]
    assert set(doc) == SUMMARY_KEYS and doc["doc_id"] == did and doc["version"] == 1
    assert doc["source"] == "upload://guide.md" and doc["parse"]["state"] == "ready"
    assert doc["chunks"] == {"total": 2, "enabled": 2}
    assert doc["indexes"]["vector"]["state"] == "unavailable"  # offline: no semantic embedder
    assert doc["indexes"]["fulltext"]["state"] == "ready"
    assert doc["indexes"]["fulltext"]["indexed"] == 2 and doc["indexes"]["graph"]["total"] == 2
    assert doc["source_artifact"] == {
        "available": True,
        "sha256": hashlib.sha256(BODY).hexdigest(),
        "media_type": doc["source_artifact"]["media_type"],
    }
    assert client.get(f"/v1/documents/{did}", headers=A).json()["data"] == doc
    assert client.get(f"/v1/documents/{did}", headers=B).status_code == 404
    assert client.get("/v1/documents", headers=R).status_code == 403
    assert client.get("/v1/documents?limit=101", headers=A).status_code == 422
    assert client.get("/v1/documents?status=bogus", headers=A).status_code == 422
    assert (
        client.get("/v1/documents?status=ready&query=guide", headers=A).json()["data"]["total"] == 1
    )
    assert client.get("/v1/documents?query=nothing", headers=A).json()["data"]["items"] == []


def test_chunk_pages_and_detail_hide_embeddings_and_paths(api):
    client, svc = api
    did = indexed_doc(client, svc)
    page = chunks(client, did).json()["data"]
    assert page["total"] == 2 and [c["ordinal"] for c in page["items"]] == [0, 1]
    first = page["items"][0]
    assert set(first) == CHUNK_KEYS and first["breadcrumb"] == ["guide.md", "Guide"]
    assert first["stages"]["fulltext"]["state"] == "ready"
    assert first["stages"]["vector"]["state"] == "unavailable"
    detail = client.get(f"/v1/documents/{did}/chunks/{first['chunk_id']}", headers=A)
    data = detail.json()["data"]
    assert "Acme makes widgets." in data["text"] and data["provenance"]["revision"] == 1
    assert "embedding" not in detail.text and "/tmp" not in detail.text
    assert chunks(client, did, headers=B).status_code == 404
    assert client.get(f"/v1/documents/{did}/chunks/nope", headers=A).status_code == 404
    assert (
        client.get(f"/v1/documents/{did}/chunks?limit=1&offset=1", headers=A).json()["data"][
            "items"
        ][0]["ordinal"]
        == 1
    )


def test_chunk_patch_is_idempotent_fenced_and_survives_restart(api, tmp_path):
    client, svc = api
    did = indexed_doc(client, svc)
    cid = chunks(client, did).json()["data"]["items"][0]["chunk_id"]
    body = {"text": "# Guide\n\nAcme makes gizmos.", "expected_revision": 1}
    assert patch(client, did, cid, body, key=None).status_code == 400
    assert patch(client, did, cid, {"expected_revision": 1}).status_code == 422
    assert patch(client, did, cid, body, headers=R).status_code == 403
    assert patch(client, did, cid, body, headers=B).status_code == 404
    accepted = patch(client, did, cid, body)
    assert accepted.status_code == 202
    data = accepted.json()["data"]
    assert data["saved_revision"] == 2 and data["index_ready"] is False and data["task_id"]
    assert set(data["chunk"]) == CHUNK_KEYS and data["chunk"]["revision"] == 2
    assert data["chunk"]["stages"]["fulltext"]["state"] == "pending"
    assert patch(client, did, cid, body).json() == accepted.json()
    assert patch(client, did, cid, {**body, "text": "different"}).status_code == 409
    stale = patch(client, did, cid, body, key="k2")
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "REVISION_CONFLICT"
    restarted = QueryService.create_offline()
    with TestClient(create_app(query_service=restarted)) as again:
        assert patch(again, did, cid, body).json() == accepted.json()
    restarted.close()
    build_inline_worker(svc).run_once()
    assert svc.bundle.fulltext.search("gizmos", tenant_id="alpha")
    assert svc.bundle.fulltext.search("widgets", tenant_id="alpha") == []
    task = client.get(f"/v1/ingest-tasks/{data['task_id']}", headers=A).json()["data"]
    assert task["status"] == "done"


def test_disable_hides_chunk_from_visibility_before_the_worker_runs(api):
    client, svc = api
    did = indexed_doc(client, svc)
    cid = chunks(client, did).json()["data"]["items"][1]["chunk_id"]
    generation = svc.bundle.visibility.generation("alpha")
    disabled = patch(client, did, cid, {"enabled": False, "expected_revision": 1})
    assert disabled.status_code == 202
    assert disabled.json()["data"]["chunk"]["stages"]["fulltext"]["state"] == "disabled"
    assert svc.bundle.visibility.visible("alpha", [(did, cid)]) == set()
    assert svc.bundle.visibility.generation("alpha") > generation
    build_inline_worker(svc).run_once()
    assert svc.bundle.fulltext.search("gadgets", tenant_id="alpha") == []
    doc = client.get(f"/v1/documents/{did}", headers=A).json()["data"]
    assert doc["chunks"] == {"total": 2, "enabled": 1}
    assert doc["indexes"]["fulltext"] == {**doc["indexes"]["fulltext"], "state": "ready"}


def test_reparse_from_the_retained_source_and_legacy_rechunk(api):
    client, svc = api
    did = indexed_doc(client, svc)
    url = f"/v1/documents/{did}/reparse"
    headers = {**A, "Idempotency-Key": "re-1"}
    assert client.post(url, headers=A, json={"expected_version": 1}).status_code == 400
    other = {**B, "Idempotency-Key": "x"}
    assert client.post(url, headers=other, json={"expected_version": 1}).status_code == 404
    accepted = client.post(url, headers=headers, json={"expected_version": 1})
    assert accepted.status_code == 202 and accepted.json()["data"]["state"] == "queued"
    pending = client.get(f"/v1/documents/{did}", headers=A).json()["data"]
    assert pending["pending_operation"]["kind"] == "reparse" and pending["version"] == 1
    build_inline_worker(svc).run_once()
    doc = client.get(f"/v1/documents/{did}", headers=A).json()["data"]
    assert doc["version"] == 2 and doc["pending_operation"] is None
    assert doc["source_artifact"]["available"] and doc["parse"]["state"] == "ready"
    stale = {**A, "Idempotency-Key": "re-2"}
    assert client.post(url, headers=stale, json={"expected_version": 1}).status_code == 409
    # Synchronous resumable completion retains the original before parts are deleted.
    resumable = _complete_session(client, b"resumable text")
    rid = resumable["docs"][0]["doc_id"]
    artifact = client.get(f"/v1/documents/{rid}", headers=A).json()["data"]["source_artifact"]
    assert artifact["sha256"] == hashlib.sha256(b"resumable text").hexdigest()


def _complete_session(client, body, *, name="legacy.txt", query=""):
    uid = client.post(
        "/v1/uploads/init", headers=A, json={"filename": name, "size": len(body)}
    ).json()["data"]["id"]
    assert client.put(f"/v1/uploads/{uid}/parts/0", headers=A, content=body).is_success
    response = client.post(f"/v1/uploads/{uid}/complete{query}", headers=A)
    assert response.status_code in {200, 202}
    return response.json()["data"]


def test_reparse_without_a_retained_source_offers_rechunk(api):
    client, svc = api
    from agentic_graphrag.core.ports.stores import DocumentRecord

    # A legacy document created before source retention existed.
    lifecycle = build_inline_worker(svc).pipeline.lifecycle
    legacy = lifecycle.accept(DocumentRecord("", "old.md", "# Old\n\nText.", tenant_id="alpha"))
    assert svc.bundle.docs.source_for(("alpha", legacy["doc_id"], 1)) is None
    url = f"/v1/documents/{legacy['doc_id']}/reparse"
    keyed = {**A, "Idempotency-Key": "a"}
    refused = client.post(url, headers=keyed, json={"expected_version": 1})
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error["code"] == "REPARSE_UNAVAILABLE"
    assert error["details"] == {"rechunk_available": True}
    rechunk = {"expected_version": 1, "mode": "rechunk"}
    assert client.post(url, headers={**A, "Idempotency-Key": "b"}, json=rechunk).status_code == 202


def test_async_upload_reserves_ids_and_publishes_through_the_worker(api):
    client, svc = api
    response = client.post(
        "/v1/docs?parse_async=true", headers=A, files={"files": ("async.md", BODY)}
    )
    assert response.status_code == 202
    task = response.json()["data"]
    reserved = task["docs"][0]["doc_id"]
    assert task["parse_async"] is True and task["docs"][0]["status"] == "parsing"
    staged = client.get(f"/v1/documents/{reserved}", headers=A).json()["data"]
    assert staged["parse"]["state"] == "queued" and staged["parse"]["task_id"] == task["id"]
    assert client.get(f"/v1/documents/{reserved}/chunks", headers=A).status_code == 404
    assert client.get("/v1/documents?status=parsing", headers=A).json()["data"]["total"] == 1
    build_inline_worker(svc).run_once()
    published = client.get(f"/v1/documents/{reserved}", headers=A).json()["data"]
    assert published["parse"]["state"] == "ready" and published["version"] == 1
    assert published["source_artifact"]["available"]
    done = client.get(f"/v1/ingest-tasks/{task['id']}", headers=A).json()["data"]
    assert done["status"] == "done" and done["docs"][0]["provisional_doc_id"] == reserved
    bad = client.post("/v1/docs?parse_async=true", headers=A, files={"files": ("evil.exe", b"MZ")})
    assert bad.status_code == 415  # validation still happens before staging


def test_async_resumable_completion_creates_one_operation(api):
    client, svc = api
    query = "?parse_async=true"
    first = _complete_session(client, b"async resumable", name="r.txt", query=query)
    uid = first["id"]
    again = client.post(f"/v1/uploads/{uid}/complete?parse_async=true", headers=A)
    assert again.status_code == 202 and again.json()["data"] == first
    assert svc.bundle.docs.get_op(uid) is not None
    _, total = svc.ingest_tasks.list(tenant_id="alpha", limit=10)
    assert total == 1
    build_inline_worker(svc).run_once()
    assert svc.ingest_tasks.get(uid).status == "done"


def test_identity_capabilities_cover_document_operations(api):
    client, _ = api
    operator = client.get("/v1/me", headers=A).json()["data"]["capabilities"]
    reader = client.get("/v1/me", headers=R).json()["data"]["capabilities"]
    keys = ("documents", "chunk_mutations", "document_reparse", "parse_async")
    assert all(operator[k] for k in keys) and not any(reader[k] for k in keys)
