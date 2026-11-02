"""RBAC, ownership and resumable document upload API contracts."""

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


@pytest.fixture
def client_service(monkeypatch, tmp_path):
    monkeypatch.setenv("AGR_REQUIRE_AUTH", "1")
    monkeypatch.setenv(
        "AGR_API_KEYS", "alpha:alpha-key:operator,beta:beta-key:operator,alpha:reader-key:reader"
    )
    monkeypatch.setenv("AGR_RATE_LIMIT_QPS", "1000")
    monkeypatch.setenv("AGR_RUNTIME_DIR", str(tmp_path))
    audit = AuditEventStore(tmp_path / "audit.jsonl")
    monkeypatch.setattr(audit_events, "_STORE", audit)
    svc = QueryService.create_offline()
    svc.cfg = svc.cfg.model_copy(deep=True)
    svc.cfg.indexing.upload_part_bytes = 4
    svc.cfg.indexing.max_upload_bytes = 12
    svc.cfg.indexing.max_file_bytes = 12
    svc.cfg.indexing.max_batch_files = 2
    with TestClient(create_app(query_service=svc)) as client:
        yield client, svc, audit
    svc.close()


def test_upload_identity_duplicate_delete_isolation_and_audit(client_service):
    client, svc, audit = client_service

    def upload(text, headers=A):
        return client.post("/v1/docs", headers=headers, files={"files": ("same.txt", text)})

    first = upload(b"evidence").json()["data"]["docs"][0]
    did = first["doc_id"]
    assert (
        first["version"] == "1"
        and first["content_sha256"] == hashlib.sha256(b"evidence").hexdigest()
    )
    assert upload(b"evidence").json()["data"]["docs"][0]["status"] == "duplicate"
    assert upload(b"evidence", B).json()["data"]["docs"][0]["doc_id"] != did
    build_inline_worker(svc).run_once()
    assert client.delete(f"/v1/documents/{did}", headers=R).status_code == 403
    assert client.delete(f"/v1/documents/{did}", headers=B).status_code == 404
    assert svc.bundle.docs.get(did, tenant_id="alpha") is not None
    assert client.delete(f"/v1/documents/{did}", headers=A).json()["data"]["status"] == "deleted"
    assert client.delete(f"/v1/documents/{did}", headers=A).status_code == 404
    assert svc.bundle.fulltext.search("evidence", tenant_id="alpha") == []
    assert audit.list_events(action="doc_delete", tenant_id="alpha")[0].outcome == "success"


def test_resumable_upload_idempotency_caps_and_ownership(client_service):
    client, svc, _ = client_service
    init = client.post("/v1/uploads/init", headers=A, json={"filename": "large.txt", "size": 6})
    assert init.status_code == 200
    uid = init.json()["data"]["id"]
    url = f"/v1/uploads/{uid}"
    assert client.get(url, headers=B).status_code == 404
    assert client.put(url + "/parts/0", headers=B, content=b"abcd").status_code == 404
    assert client.post(url + "/complete", headers=B).status_code == 404
    assert client.post(url + "/complete", headers=A).status_code == 409
    assert client.put(url + "/parts/0", headers=A, content=b"abcde").status_code == 413
    assert client.put(url + "/parts/0", headers=A, content=b"abcd").status_code == 200
    assert client.put(url + "/parts/0", headers=A, content=b"abcd").status_code == 200
    assert client.put(url + "/parts/0", headers=A, content=b"xxxx").status_code == 409
    assert client.put(url + "/parts/9", headers=A, content=b"ef").status_code == 409
    assert client.get(url, headers=A).json()["data"]["received"] == [0]
    assert client.put(url + "/parts/1", headers=A, content=b"ef").status_code == 200
    complete = client.post(url + "/complete", headers=A)
    assert complete.status_code == 200
    assert client.post(url + "/complete", headers=A).json() == complete.json()
    did = complete.json()["data"]["docs"][0]["doc_id"]
    assert svc.bundle.docs.get(did, tenant_id="alpha").content == "abcdef"
    assert client.put(url + "/parts/1", headers=A, content=b"ef").status_code == 409
    assert (
        client.post(
            "/v1/uploads/init", headers=A, json={"filename": "x.txt", "size": 13}
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/v1/uploads/init", headers=R, json={"filename": "x.txt", "size": 1}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/uploads/init", headers=A, json={"filename": "x.pdf", "size": 1}
        ).status_code
        == 413
    )


def test_configured_normal_upload_caps(client_service):
    client, _, _ = client_service
    assert (
        client.post("/v1/docs", headers=A, files={"files": ("x.txt", b"a" * 13)}).status_code == 413
    )
    files = [("files", (f"{i}.txt", b"x")) for i in range(3)]
    assert client.post("/v1/docs", headers=A, files=files).status_code == 413


def test_complete_rejects_hash_mismatch_and_invalid_utf8(client_service):
    client, _, _ = client_service
    for body, digest in [(b"x", "0" * 64), (b"\xff", "")]:
        uid = client.post(
            "/v1/uploads/init",
            headers=A,
            json={"filename": "x.txt", "size": 1, "content_sha256": digest},
        ).json()["data"]["id"]
        client.put(f"/v1/uploads/{uid}/parts/0", headers=A, content=body)
        assert client.post(f"/v1/uploads/{uid}/complete", headers=A).status_code in {409, 413}


def test_upload_session_restart_and_expiry_are_tenant_scoped(tmp_path, monkeypatch):
    import agentic_graphrag.knowledge.resumable_upload as module
    from agentic_graphrag.config_ingest import IndexingConfig

    cfg = IndexingConfig(upload_part_bytes=4, upload_expiry_seconds=10)
    monkeypatch.setattr(module.time, "time", lambda: 100)
    sessions = module.UploadSessions(tmp_path, cfg)
    first = sessions.init("alpha", name="x.txt", size=1)
    other = sessions.init("beta", name="x.txt", size=1)
    sessions.put(first["id"], "alpha", number=0, body=b"x")
    restarted = module.UploadSessions(tmp_path, cfg)
    assert restarted.content(first["id"], "alpha") == b"x"
    monkeypatch.setattr(module.time, "time", lambda: 111)
    with pytest.raises(KeyError):
        restarted.get(first["id"], "alpha")
    assert restarted.prune(tenant_id="alpha") == 1
    monkeypatch.setattr(module.time, "time", lambda: 100)
    assert restarted.get(other["id"], "beta")["name"] == "x.txt"


def test_upload_acceptance_keeps_event_loop_responsive(client_service, monkeypatch):
    import asyncio
    import io
    import threading

    from starlette.datastructures import UploadFile

    from agentic_graphrag.api.routes.knowledge_upload import save_uploads
    from agentic_graphrag.knowledge.document_lifecycle import DocumentLifecycle

    _, svc, _ = client_service
    entered, released = threading.Event(), threading.Event()
    accept = DocumentLifecycle.accept
    observed = []

    def blocking_accept(self, doc):
        entered.set()
        released.wait(timeout=2)
        return accept(self, doc)

    monkeypatch.setattr(DocumentLifecycle, "accept", blocking_accept)

    async def probe():
        while not entered.is_set():
            await asyncio.sleep(0)
        observed.append(not released.is_set())
        released.set()

    async def run():
        await asyncio.gather(
            save_uploads(
                svc,
                [UploadFile(io.BytesIO(b"evidence"), filename="x.txt")],
                tenant_id="beta",
                task_id="test",
            ),
            probe(),
        )

    # Watchdog prevents a broken event loop from hanging the regression test.
    watchdog = threading.Timer(1, released.set)
    watchdog.start()
    try:
        asyncio.run(run())
    finally:
        released.set()
        watchdog.cancel()
    assert observed == [True]
