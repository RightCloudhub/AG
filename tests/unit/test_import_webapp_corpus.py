"""The corpus CLI validates all inputs and imports through the selected tenant's API."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/import_webapp_corpus.py"
SPEC = importlib.util.spec_from_file_location("import_webapp_corpus", SCRIPT)
assert SPEC and SPEC.loader
cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cli
SPEC.loader.exec_module(cli)

TEST_SECRET = "Only-a-test-password-42!"


class ImportAPI:
    def __init__(self):
        self.tenant = "personal"
        self.role = "admin"
        self.csrf = "login-csrf"
        self.status = "done"
        self.save_error = False
        self.expire_once = False
        self.expire_logout = False
        self.expired = False
        self.timeout_upload = False
        self.calls = []
        self.batches = []
        self.tasks = {}

    def identity(self):
        return {
            "user_id": "user-1",
            "tenant_id": self.tenant,
            "role": self.role,
            "capabilities": {"knowledge": self.role in {"admin", "operator"}},
            "csrf_token": self.csrf,
        }

    def response(self, data=None, *, status=200, code="ERROR"):
        return httpx.Response(
            status,
            json={
                "success": status < 400,
                "data": data,
                "error": {"code": code},
            },
        )

    def handle(self, request):
        path = request.url.path
        self.calls.append((request.method, path))
        if path == "/v1/auth/csrf":
            return self.response({"csrf_token": "anonymous-csrf"})
        if path == "/v1/auth/login":
            assert request.headers["Origin"] == "http://localhost:8000"
            assert request.headers["X-CSRF-Token"] == "anonymous-csrf"
            assert json.loads(request.content)["password"] == TEST_SECRET
            return self.response(self.identity())
        if path == "/v1/me":
            return self.response(self.identity())
        if path.startswith("/v1/tenants/"):
            assert request.headers["X-AGR-Tenant"] == "personal"
            self.tenant = "team"
            self.csrf = "switched-csrf"
            return self.response(self.identity())
        if path == "/v1/auth/refresh":
            assert "X-AGR-User" not in request.headers
            assert "X-AGR-Tenant" not in request.headers
            self.csrf = "refreshed-csrf"
            return self.response(self.identity())
        if path == "/v1/auth/logout":
            if self.expire_logout and not self.expired:
                self.expired = True
                return self.response(status=401, code="UNAUTHORIZED")
            assert request.headers["X-CSRF-Token"] == self.csrf
            return self.response({"logged_out": True})
        if path == "/v1/docs":
            return self.upload(request)
        if path.startswith("/v1/ingest-tasks/"):
            task = self.tasks[path.rsplit("/", 1)[1]]
            return self.response({**task, "status": self.status, "message": "Processed"})
        pytest.fail(f"Unexpected API call: {request.method} {path}")

    def upload(self, request):
        if self.timeout_upload:
            raise httpx.ReadTimeout("Uncertain submission", request=request)
        if self.expire_once and not self.expired:
            self.expired = True
            return self.response(status=401, code="UNAUTHORIZED")
        if request.headers.get("Authorization"):
            assert request.headers["Authorization"] == "Bearer test-service-key"
        else:
            assert request.headers["X-AGR-Tenant"] == self.tenant
            assert request.headers["X-CSRF-Token"] == self.csrf
        parts = request.content.split(b'filename="')[1:]
        names = [part.split(b'"', 1)[0].decode() for part in parts]
        self.batches.append(names)
        task = {
            "id": f"task-{len(self.batches)}",
            "tenant_id": self.tenant,
            "status": "queued",
            "docs": [{"doc_id": name} for name in names],
        }
        if self.save_error:
            task["docs"][0]["error"] = "save_failed"
        self.tasks[task["id"]] = task
        return self.response(task)


@pytest.fixture
def importer(tmp_path, monkeypatch):
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "first.md").write_text("Elena Varga is the CEO of Apex Holdings.")
    api = ImportAPI()
    client = cli.WebClient("http://localhost:8000")
    client.http.close()
    client.http = httpx.Client(
        base_url="http://localhost:8000", transport=httpx.MockTransport(api.handle)
    )
    monkeypatch.setattr(cli, "WebClient", lambda origin: client)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: TEST_SECRET)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    report = tmp_path / "report.json"
    args = [str(source), "--email", "person@example.com", "--report", str(report)]
    yield api, args, source, report
    client.http.close()


def test_import_batches_select_tenant_and_revoke_only_script_session(importer):
    api, args, source, report = importer
    for index in range(20):
        (source / f"extra-{index:02}.txt").write_text("A source document.")
    assert cli.main([*args, "--tenant-id", "team"]) == 0
    assert [len(batch) for batch in api.batches] == [20, 1]
    output = json.loads(report.read_text())
    assert output["status"] == "processed"
    assert output["submitted_documents"] == 21
    assert output["tenant_id"] == "team"
    assert output["status_counts"] == {"done": 2}
    assert all(task["tenant_id"] == "team" for task in output["tasks"])
    assert api.calls[-1] == ("POST", "/v1/auth/logout")
    assert TEST_SECRET not in report.read_text() and api.csrf not in report.read_text()


def test_preflight_is_offline_and_excludes_runtime_files(importer, monkeypatch):
    api, _args, source, report = importer
    (source / "identity.db").write_bytes(b"not a document")
    (source / "signing.key").write_bytes(b"not a document")
    (source / "alias.md").symlink_to(source / "first.md")
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: pytest.fail("Unexpected password prompt"))
    assert cli.main([str(source), "--dry-run", "--report", str(report)]) == 0
    assert api.calls == []
    result = json.loads(report.read_text())
    assert [document["name"] for document in result["files"]] == ["first.md"]


@pytest.mark.parametrize("kind", ["duplicate", "invalid_utf8", "too_large", "missing"])
def test_invalid_sources_never_authenticate_or_partially_upload(importer, kind):
    api, args, source, _report = importer
    if kind == "duplicate":
        nested = source / "nested"
        nested.mkdir()
        (nested / "first.md").write_text("A different document.")
    elif kind == "invalid_utf8":
        (source / "last.md").write_bytes(b"\xff")
    elif kind == "too_large":
        (source / "last.md").write_bytes(b"x" * (cli.MAX_FILE_BYTES + 1))
    else:
        args[0] = str(source / "missing")
    assert cli.main(args) == 1
    assert api.calls == []


def test_overlapping_sources_are_deduplicated_and_changed_files_fail(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("Original")
    documents = cli.collect_documents([tmp_path, path])
    assert len(documents) == 1
    path.write_text("Modified")
    with pytest.raises(cli.ImportFailure, match="changed after validation"):
        documents[0].content()


def test_report_cannot_overwrite_a_source_document(importer):
    _api, _args, source, _report = importer
    document = source / "first.md"
    original = document.read_bytes()
    assert cli.main([str(source), "--dry-run", "--report", str(document)]) == 1
    assert document.read_bytes() == original


def test_no_wait_records_submission_without_claiming_completion(importer):
    api, args, _source, report = importer
    assert cli.main([*args, "--no-wait"]) == 0
    output = json.loads(report.read_text())
    assert output["status"] == "submitted" and output["status_counts"] == {"queued": 1}
    assert not any("ingest-tasks" in path for _method, path in api.calls)


@pytest.mark.parametrize("problem", ["save_error", "failed", "timeout"])
def test_failed_import_retains_task_details_and_exits_nonzero(importer, monkeypatch, problem):
    api, args, _source, report = importer
    if problem == "save_error":
        api.save_error = True
    elif problem == "failed":
        api.status = "failed"
    else:
        api.status = "queued"
        ticks = iter([0, 0, 2])
        monkeypatch.setattr(cli.time, "monotonic", lambda: next(ticks))
        args += ["--wait-timeout", "1"]
    assert cli.main(args) == 1
    output = json.loads(report.read_text())
    assert output["status"] == "failed"
    assert output["tasks"][0]["id"] == "task-1"
    assert api.calls[-1] == ("POST", "/v1/auth/logout")


def test_review_is_reported_without_approving_it(importer):
    api, args, _source, report = importer
    api.status = "review"
    assert cli.main(args) == 0
    assert json.loads(report.read_text())["status_counts"] == {"review": 1}
    assert not any("decision" in path for _method, path in api.calls)


def test_network_timeout_does_not_replay_an_uncertain_upload(importer):
    api, args, _source, report = importer
    api.timeout_upload = True
    assert cli.main(args) == 1
    assert api.calls.count(("POST", "/v1/docs")) == 1
    assert json.loads(report.read_text())["error"] == "ReadTimeout"


def test_expired_access_refreshes_once_before_retrying_denied_upload(importer):
    api, args, _source, _report = importer
    api.expire_once = True
    assert cli.main(args) == 0
    assert api.calls.count(("POST", "/v1/auth/refresh")) == 1
    assert api.calls.count(("POST", "/v1/docs")) == 2
    assert len(api.batches) == 1


def test_reader_is_rejected_before_upload(importer):
    api, args, _source, _report = importer
    api.role = "reader"
    assert cli.main(args) == 1
    assert api.batches == []


def test_cleanup_refreshes_expired_access_before_scoped_logout(importer):
    api, args, _source, _report = importer
    api.expire_logout = True
    assert cli.main(args) == 0
    assert api.calls.count(("POST", "/v1/auth/refresh")) == 1
    assert api.calls.count(("POST", "/v1/auth/logout")) == 2


def test_service_key_cannot_switch_to_a_different_tenant(importer, monkeypatch):
    api, _args, source, report = importer
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("test-service-key\n"))
    assert (
        cli.main(
            [
                str(source),
                "--api-key-stdin",
                "--tenant-id",
                "other",
                "--report",
                str(report),
            ]
        )
        == 1
    )
    assert api.calls == [("GET", "/v1/me")]
    assert "test-service-key" not in report.read_text()


def test_password_stdin_and_actionable_origin_error(importer, monkeypatch, capsys):
    api, args, _source, report = importer
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(TEST_SECRET + "\n"))
    api.response = lambda *a, **kw: httpx.Response(
        403,
        json={
            "success": False,
            "error": {"code": "CSRF_FAILED"},
        },
    )
    assert cli.main([*args, "--password-stdin"]) == 1
    assert "AGR_WEB_ORIGIN" in capsys.readouterr().err
    assert TEST_SECRET not in report.read_text()


def test_password_can_be_read_from_stdin(importer, monkeypatch):
    _api, args, _source, _report = importer
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(TEST_SECRET + "\n"))
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: pytest.fail("Unexpected password prompt"))
    assert cli.main([*args, "--password-stdin"]) == 0


def test_service_key_import_uses_no_browser_session(importer, monkeypatch):
    api, _args, source, report = importer
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("test-service-key\n"))
    assert cli.main([str(source), "--api-key-stdin", "--report", str(report)]) == 0
    assert len(api.batches) == 1
    assert not any(path.startswith("/v1/auth/") for _method, path in api.calls)
    assert "test-service-key" not in report.read_text()


def test_report_write_error_fails_cleanly_before_authentication(importer, monkeypatch, capsys):
    api, args, _source, _report = importer

    def deny_write(*args, **kwargs):
        raise PermissionError("Read-only output directory")

    monkeypatch.setattr(cli, "save_report", deny_write)
    assert cli.main(args) == 1
    assert "PermissionError" in capsys.readouterr().err
    assert api.calls == []


@pytest.mark.parametrize(
    "value",
    [
        "ftp://localhost",
        "http://user:password@localhost",
        "http://localhost/path",
        "http://localhost?token=secret",
        "http://localhost:bad",
        "http://[broken",
        "http://localhost:99999",
        "http://localhost:0",
    ],
)
def test_url_must_be_a_valid_origin(value):
    with pytest.raises(SystemExit):
        cli.arguments(["--dry-run", "--url", value])
