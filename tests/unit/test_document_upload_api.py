"""RBAC, ownership and resumable document upload API contracts."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.routes import document_routes
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.app.write_path import build_inline_worker
from agentic_graphrag.governance import audit_events
from agentic_graphrag.governance.audit_events import AuditEventStore
from agentic_graphrag.knowledge.resumable_upload import UploadSessions

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


def open_session(client, body, name="x.md"):
    uid = client.post(
        "/v1/uploads/init", headers=A, json={"filename": name, "size": len(body)}
    ).json()["data"]["id"]
    for number, start in enumerate(range(0, len(body), 4)):
        part = body[start : start + 4]
        assert client.put(f"/v1/uploads/{uid}/parts/{number}", headers=A, content=part).is_success
    return uid


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
        == 415  # MinerU-only type while MinerU is disabled: rejected before any part
    )


def test_configured_normal_upload_caps(client_service):
    client, _, _ = client_service
    assert (
        client.post("/v1/docs", headers=A, files={"files": ("x.txt", b"a" * 13)}).status_code == 413
    )
    files = [("files", (f"{i}.txt", b"x")) for i in range(3)]
    assert client.post("/v1/docs", headers=A, files=files).status_code == 413


def test_complete_rejects_hash_mismatch_and_undecodable_text(client_service):
    client, _, _ = client_service
    # Completion parses like a direct upload: detectable encodings are accepted,
    # undecodable bytes are a 422 parse error.
    for body, digest, status in [(b"x", "0" * 64, 409), (b"\xff\xfe\x00", "", 422)]:
        uid = client.post(
            "/v1/uploads/init",
            headers=A,
            json={"filename": "x.txt", "size": len(body), "content_sha256": digest},
        ).json()["data"]["id"]
        client.put(f"/v1/uploads/{uid}/parts/0", headers=A, content=body)
        assert client.post(f"/v1/uploads/{uid}/complete", headers=A).status_code == status


def test_resumable_completion_parses_like_a_direct_upload(client_service):
    client, svc, _ = client_service
    body = "\ufeffhi".encode()  # a UTF-8 BOM plus text, sent as two parts
    uid = open_session(client, body, name="bom.txt")
    complete = client.post(f"/v1/uploads/{uid}/complete", headers=A)
    assert complete.status_code == 200
    doc = svc.bundle.docs.get(complete.json()["data"]["docs"][0]["doc_id"], tenant_id="alpha")
    assert doc.content == "hi" and doc.metadata["parser_name"] == "text"
    assert doc.metadata["source_encoding"] == "utf-8-sig"


def test_completion_parses_outside_the_session_mutex(client_service, monkeypatch, tmp_path):
    import fcntl

    client, _, _ = client_service
    uid = open_session(client, b"abc")
    parse = document_routes.parse_upload_content
    mutex_free = []

    def spy(svc, filename, content):
        with (tmp_path / "uploads" / ".uploads.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                mutex_free.append(False)
            else:
                mutex_free.append(True)
                fcntl.flock(handle, fcntl.LOCK_UN)
        return parse(svc, filename, content)

    monkeypatch.setattr(document_routes, "parse_upload_content", spy)
    assert client.post(f"/v1/uploads/{uid}/complete", headers=A).status_code == 200
    assert mutex_free == [True]


def test_part_writes_wait_for_the_mutex_off_the_event_loop(client_service, monkeypatch):
    import asyncio

    client, _, _ = client_service
    put = UploadSessions.put
    on_loop = []

    def spy(self, *args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return put(self, *args, **kwargs)

    monkeypatch.setattr(UploadSessions, "put", spy)
    open_session(client, b"abc")
    assert on_loop == [False]


def test_completion_retry_after_a_crash_reuses_the_task_without_parsing(
    client_service, monkeypatch
):
    client, svc, _ = client_service
    uid = open_session(client, b"abc")
    finish = UploadSessions.finish

    def crash(*args, **kwargs):
        raise RuntimeError("crash before the result was recorded")

    monkeypatch.setattr(UploadSessions, "finish", crash)
    with pytest.raises(RuntimeError):
        client.post(f"/v1/uploads/{uid}/complete", headers=A)
    assert svc.ingest_tasks.get(uid, tenant_id="alpha") is not None
    monkeypatch.setattr(UploadSessions, "finish", finish)
    monkeypatch.setattr(
        document_routes, "parse_upload_content", lambda *args: pytest.fail("parsed again")
    )
    retried = client.post(f"/v1/uploads/{uid}/complete", headers=A)
    assert retried.status_code == 200 and retried.json()["data"]["id"] == uid


@pytest.mark.parametrize(
    "name,body,status", [("bad.txt", b"\xff\xfe\x00", 422), ("evil.exe", b"MZ", 415)]
)
def test_rejected_file_leaves_the_whole_batch_unaccepted(client_service, name, body, status):
    client, svc, _ = client_service
    first = client.post("/v1/docs", headers=A, files={"files": ("a.txt", b"evidence")})
    did = first.json()["data"]["docs"][0]["doc_id"]
    build_inline_worker(svc).run_once()
    batch = [("files", ("a.txt", b"replacement")), ("files", (name, body))]
    assert client.post("/v1/docs", headers=A, files=batch).status_code == status
    doc = svc.bundle.docs.get(did, tenant_id="alpha")
    assert doc.content == "evidence" and doc.version == 1
    assert svc.bundle.fulltext.search("evidence", tenant_id="alpha")
    # Resending the good file is a normal replacement, not a stranded duplicate.
    assert client.post("/v1/docs", headers=A, files={"files": ("a.txt", b"replacement")}).is_success
    build_inline_worker(svc).run_once()
    assert svc.bundle.docs.get(did, tenant_id="alpha").version == 2
    assert svc.bundle.fulltext.search("replacement", tenant_id="alpha")


def test_failed_delete_is_recorded_and_left_pending(client_service, monkeypatch, caplog):
    import logging

    client, svc, audit = client_service
    uploaded = client.post("/v1/docs", headers=A, files={"files": ("x.txt", b"evidence")})
    did = uploaded.json()["data"]["docs"][0]["doc_id"]

    def down(*args, **kwargs):
        raise OSError("index backend down")

    monkeypatch.setattr(svc.bundle.fulltext, "delete_by_doc", down)
    with caplog.at_level(logging.WARNING):
        assert client.delete(f"/v1/documents/{did}", headers=A).status_code == 503
    records = [r for r in caplog.records if getattr(r, "component", None) == "document_lifecycle"]
    assert len(records) == 1 and records[0].doc_id == did and records[0].error_type == "OSError"
    assert audit.list_events(action="doc_delete", tenant_id="alpha")[0].outcome == "partial"
    assert svc.bundle.docs.get(did, tenant_id="alpha").metadata["lifecycle"] == "deleting"


def test_mineru_types_open_a_session_only_when_mineru_is_configured(client_service):
    client, svc, _ = client_service
    init = {"filename": "scan.pdf", "size": 1}
    refused = client.post("/v1/uploads/init", headers=A, json=init)
    assert refused.status_code == 415 and "MinerU" in refused.text
    svc.cfg.knowledge.parsing.mineru.enabled = True
    svc.cfg.knowledge.parsing.mineru.endpoint = "http://mineru.test/file_parse"
    assert client.post("/v1/uploads/init", headers=A, json=init).status_code == 200


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
