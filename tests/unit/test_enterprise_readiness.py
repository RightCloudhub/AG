"""ENT enterprise readiness tests (ENT-01 through ENT-08, except ENT-07)."""

from __future__ import annotations

import json
import logging
import time

import pytest
from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.auth import Principal, parse_api_keys
from agentic_graphrag.api.rbac import (
    KeyInfo,
    KeyRegistry,
    Role,
    check_key_expiry,
    parse_api_keys_with_roles,
)
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.observability.audit_events import (
    AUTH_FAILURE,
    BUDGET_EXCEEDED,
    DOC_UPLOAD,
    RATE_LIMITED,
    REVIEW_DECISION,
    AuditEvent,
    AuditEventStore,
    emit_audit_event,
)
from agentic_graphrag.observability.logging_setup import (
    JSONFormatter,
    bind_context,
    clear_context,
    request_id_var,
)
from agentic_graphrag.observability.redaction import (
    Redactor,
    redact_audit_payload,
    redact_log_record,
    reset_redactor,
)

# ---------------------------------------------------------------------------
# ENT-01: Structured logging
# ---------------------------------------------------------------------------


class TestENT01Logging:
    """ENT-01: structured JSON logging with context propagation."""

    def test_json_formatter_fields(self) -> None:
        bind_context(request_id="r1", tenant_id="t1", query_id="q1", user_id="u1")
        fmt = JSONFormatter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "hello", (), None)
        parsed = json.loads(fmt.format(record))
        assert parsed["request_id"] == "r1"
        assert parsed["tenant_id"] == "t1"
        assert parsed["query_id"] == "q1"
        assert parsed["user_id"] == "u1"
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "hello"
        clear_context()

    def test_error_includes_stacktrace(self) -> None:
        fmt = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info = sys.exc_info()
        record = logging.LogRecord("x", logging.ERROR, "", 0, "fail", (), exc_info)
        parsed = json.loads(fmt.format(record))
        assert "stacktrace" in parsed
        assert "boom" in parsed["stacktrace"]

    def test_warning_no_stacktrace(self) -> None:
        fmt = JSONFormatter()
        try:
            raise ValueError("w")
        except ValueError:
            import sys

            exc_info = sys.exc_info()
        record = logging.LogRecord("x", logging.WARNING, "", 0, "w", (), exc_info)
        parsed = json.loads(fmt.format(record))
        assert "stacktrace" not in parsed

    def test_context_bind_and_clear(self) -> None:
        bind_context(request_id="r2")
        assert request_id_var.get() == "r2"
        clear_context()
        assert request_id_var.get() == ""


# ---------------------------------------------------------------------------
# ENT-03: Audit events
# ---------------------------------------------------------------------------


class TestENT03AuditEvents:
    """ENT-03: security/management event audit stream."""

    def test_audit_event_roundtrip(self, tmp_path) -> None:
        store = AuditEventStore(tmp_path / "events.jsonl")
        event = AuditEvent(
            ts=time.time(),
            action=AUTH_FAILURE,
            tenant_id="acme",
            user_id="u1",
            target="/v1/query",
            outcome="denied",
            details={"reason": "bad key"},
        )
        store.record(event)
        events = store.list_events(action=AUTH_FAILURE)
        assert len(events) == 1
        assert events[0].tenant_id == "acme"
        assert events[0].action == AUTH_FAILURE

    def test_five_event_types(self, tmp_path) -> None:
        store = AuditEventStore(tmp_path / "events.jsonl")
        for action in [AUTH_FAILURE, RATE_LIMITED, BUDGET_EXCEEDED, REVIEW_DECISION, DOC_UPLOAD]:
            store.record(
                AuditEvent(
                    ts=time.time(),
                    action=action,
                    tenant_id="t",
                    user_id="u",
                    target="x",
                    outcome="y",
                )
            )
        all_events = store.list_events()
        assert len(all_events) == 5
        actions = {e.action for e in all_events}
        assert actions == {AUTH_FAILURE, RATE_LIMITED, BUDGET_EXCEEDED, REVIEW_DECISION, DOC_UPLOAD}

    def test_filter_by_tenant(self, tmp_path) -> None:
        store = AuditEventStore(tmp_path / "events.jsonl")
        store.record(
            AuditEvent(ts=1.0, action="a", tenant_id="t1", user_id="u", target="", outcome="")
        )
        store.record(
            AuditEvent(ts=2.0, action="a", tenant_id="t2", user_id="u", target="", outcome="")
        )
        assert len(store.list_events(tenant_id="t1")) == 1
        assert len(store.list_events(tenant_id="t2")) == 1

    def test_rotation(self, tmp_path) -> None:
        store = AuditEventStore(tmp_path / "events.jsonl", max_bytes=50, max_backups=2)
        for i in range(20):
            store.record(
                AuditEvent(
                    ts=float(i), action="a", tenant_id="t", user_id="u", target="x", outcome="y"
                )
            )
        # At least one backup should have been created
        backups = list(tmp_path.glob("events.jsonl.*"))
        assert len(backups) >= 1

    def test_reload_from_disk(self, tmp_path) -> None:
        path = tmp_path / "events.jsonl"
        store1 = AuditEventStore(path)
        store1.record(
            AuditEvent(ts=1.0, action="a", tenant_id="t", user_id="u", target="x", outcome="y")
        )
        store2 = AuditEventStore(path)
        assert len(store2.list_events()) == 1


# ---------------------------------------------------------------------------
# ENT-04: RBAC
# ---------------------------------------------------------------------------


class TestENT04RBAC:
    """ENT-04: role-based access control and key governance."""

    def test_parse_three_segment(self) -> None:
        keys = parse_api_keys_with_roles("acme:sk-123:admin")
        assert "sk-123" in keys
        assert keys["sk-123"].tenant_id == "acme"
        assert keys["sk-123"].role == Role.ADMIN

    def test_parse_two_segment_backward_compat(self) -> None:
        keys = parse_api_keys_with_roles("acme:sk-456")
        assert keys["sk-456"].tenant_id == "acme"
        assert keys["sk-456"].role == Role.READER

    def test_parse_single_segment(self) -> None:
        keys = parse_api_keys_with_roles("sk-bare")
        assert keys["sk-bare"].tenant_id == "default"
        assert keys["sk-bare"].role == Role.READER

    def test_parse_mixed(self) -> None:
        keys = parse_api_keys_with_roles("acme:k1:admin,beta:k2,k3")
        assert keys["k1"].role == Role.ADMIN
        assert keys["k2"].role == Role.READER
        assert keys["k3"].role == Role.READER

    def test_backward_compat_parse_api_keys(self) -> None:
        m = parse_api_keys("acme:secret1:admin,beta:secret2")
        assert m["secret1"] == "acme"
        assert m["secret2"] == "beta"

    def test_key_expiry_valid(self) -> None:
        info = KeyInfo(tenant_id="t", expires_at=time.time() + 3600)
        assert check_key_expiry(info) is True

    def test_key_expiry_expired(self) -> None:
        info = KeyInfo(tenant_id="t", expires_at=time.time() - 1)
        assert check_key_expiry(info) is False

    def test_key_expiry_none(self) -> None:
        info = KeyInfo(tenant_id="t")
        assert check_key_expiry(info) is True

    def test_invalid_role_defaults_reader(self) -> None:
        keys = parse_api_keys_with_roles("acme:k1:superadmin")
        assert keys["k1"].role == Role.READER

    def test_rbac_403_when_auth_required(self, monkeypatch) -> None:
        monkeypatch.setenv("AGR_REQUIRE_AUTH", "1")
        monkeypatch.setenv("AGR_API_KEYS", "acme:reader-key:reader,acme:admin-key:admin")
        svc = QueryService.create_offline()
        app = create_app(query_service=svc)
        client = TestClient(app)
        # reader can query
        r = client.post(
            "/v1/query",
            json={"question": "Who is the CEO of Apex Holdings?"},
            headers={"Authorization": "Bearer reader-key"},
        )
        assert r.status_code == 200
        # reader cannot access metrics
        r2 = client.get("/v1/metrics", headers={"Authorization": "Bearer reader-key"})
        assert r2.status_code == 403
        # admin can access metrics
        r3 = client.get("/v1/metrics", headers={"Authorization": "Bearer admin-key"})
        assert r3.status_code == 200
        svc.close()

    def test_principal_carries_role(self) -> None:
        p = Principal(tenant_id="t", api_key="k", role=Role.ADMIN)
        assert p.role == Role.ADMIN

    def test_key_registry_env_and_yaml(self, tmp_path) -> None:
        import yaml

        yaml_path = tmp_path / "keys.yaml"
        yaml_path.write_text(
            yaml.dump(
                {
                    "keys": [
                        {"tenant": "acme", "key": "yaml-k1", "role": "operator"},
                        {
                            "tenant": "beta",
                            "key": "yaml-k2",
                            "role": "admin",
                            "expires_at": time.time() + 3600,
                            "note": "test key",
                        },
                    ]
                }
            )
        )
        reg = KeyRegistry()
        reg.load_from_env("acme:env-k1:reader")
        reg.load_from_yaml(str(yaml_path))
        assert reg.get("env-k1") is not None
        assert reg.get("env-k1").role == Role.READER
        assert reg.get("yaml-k1") is not None
        assert reg.get("yaml-k1").role == Role.OPERATOR


# ---------------------------------------------------------------------------
# ENT-06: Data security — redaction
# ---------------------------------------------------------------------------


class TestENT06Redaction:
    """ENT-06: PII redaction hooks."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        reset_redactor()
        yield
        reset_redactor()

    def test_redact_email(self) -> None:
        r = Redactor(enabled=True)
        assert r.redact("contact user@example.com now") == "contact [REDACTED_EMAIL] now"

    def test_redact_phone_cn(self) -> None:
        r = Redactor(enabled=True)
        assert "[REDACTED_PHONE]" in r.redact("call 13912345678")

    def test_redact_id_cn(self) -> None:
        r = Redactor(enabled=True)
        assert "[REDACTED_ID]" in r.redact("ID: 110101199003076543")

    def test_redact_disabled_noop(self) -> None:
        r = Redactor(enabled=False)
        text = "user@example.com"
        assert r.redact(text) == text

    def test_redact_dict(self) -> None:
        r = Redactor(enabled=True)
        data = {"msg": "email user@test.com", "nested": {"val": "13912345678"}}
        result = r.redact_dict(data)
        assert "[REDACTED_EMAIL]" in result["msg"]
        assert "[REDACTED_PHONE]" in result["nested"]["val"]
        # Original not mutated
        assert "user@test.com" in data["msg"]

    def test_redact_log_record_hook(self, monkeypatch) -> None:
        monkeypatch.setenv("AGR_REDACTION_ENABLED", "1")
        reset_redactor()
        record = {"message": "contact user@test.com"}
        result = redact_log_record(record)
        assert "[REDACTED_EMAIL]" in result["message"]

    def test_redact_audit_payload(self, monkeypatch) -> None:
        monkeypatch.setenv("AGR_REDACTION_ENABLED", "1")
        reset_redactor()
        payload = {"question": "Email is user@test.com", "answer": "OK"}
        result = redact_audit_payload(payload)
        assert "[REDACTED_EMAIL]" in result["question"]


# ---------------------------------------------------------------------------
# ENT-02 + ENT-08: Admin routes, Prometheus, healthz
# ---------------------------------------------------------------------------


class TestENT02And08AdminRoutes:
    """ENT-02: troubleshooting endpoints; ENT-08: Prometheus."""

    @pytest.fixture()
    def client(self):
        svc = QueryService.create_offline()
        app = create_app(query_service=svc)
        c = TestClient(app)
        yield c
        svc.close()

    def test_healthz_includes_review_queue(self, client) -> None:
        r = client.get("/healthz")
        assert r.status_code == 200
        checks = r.json()["checks"]
        assert "review_queue_pending" in checks

    def test_prometheus_endpoint(self, client) -> None:
        # First make a query to generate metrics
        client.post("/v1/query", json={"question": "Who is the CEO of Apex Holdings?"})
        r = client.get("/metrics-prom")
        assert r.status_code == 200
        text = r.text
        assert "agr_queries_total" in text
        assert "agr_latency_p50_ms" in text
        assert "agr_budget_trips_total" in text
        # Check Prometheus format
        for line in text.strip().split("\n"):
            if line.startswith("#"):
                assert line.startswith("# HELP") or line.startswith("# TYPE")

    def test_traces_endpoint(self, client) -> None:
        # Run a query to generate a trace
        r = client.post("/v1/query", json={"question": "Who is the CEO of Apex Holdings?"})
        qid = r.json()["data"]["query_id"]
        # Without auth required, admin endpoints are accessible
        r2 = client.get(f"/v1/traces/{qid}")
        assert r2.status_code == 200
        data = r2.json()["data"]
        assert data["query_id"] == qid

    def test_traces_404(self, client) -> None:
        r = client.get("/v1/traces/nonexistent-id")
        assert r.status_code == 404

    def test_budget_snapshot(self, client) -> None:
        r = client.get("/v1/budget/snapshot")
        assert r.status_code == 200
        assert r.json()["success"] is True

    def test_audit_events_endpoint(self, client) -> None:
        # Emit some test events
        emit_audit_event(AUTH_FAILURE, tenant_id="t1", target="/v1/query", outcome="denied")
        r = client.get("/v1/audit-events")
        assert r.status_code == 200
        assert r.json()["success"] is True


# ---------------------------------------------------------------------------
# ENT-06: Upload governance
# ---------------------------------------------------------------------------


class TestENT06UploadGovernance:
    """ENT-06: file upload size/type limits."""

    @pytest.fixture()
    def client(self):
        svc = QueryService.create_offline()
        app = create_app(query_service=svc)
        c = TestClient(app)
        yield c
        svc.close()

    def test_upload_allowed_extension(self, client) -> None:
        r = client.post(
            "/v1/docs",
            files=[("files", ("test.md", b"# Hello\nWorld", "text/plain"))],
        )
        assert r.status_code == 200

    def test_upload_disallowed_extension(self, client) -> None:
        r = client.post(
            "/v1/docs",
            files=[("files", ("evil.exe", b"MZ...", "application/octet-stream"))],
        )
        assert r.status_code == 400

    def test_upload_too_many_files(self, client) -> None:
        files = [("files", (f"f{i}.txt", b"x", "text/plain")) for i in range(25)]
        r = client.post("/v1/docs", files=files)
        assert r.status_code == 413


# ---------------------------------------------------------------------------
# ENT-05: config-based budget limits (partial — per-tenant from YAML)
# ---------------------------------------------------------------------------


class TestENT05BudgetConfig:
    """ENT-05: verify budget defaults and snapshot accessibility."""

    def test_multi_budget_snapshot(self) -> None:
        from agentic_graphrag.llm.budget_policy import MultiLevelBudget

        mb = MultiLevelBudget()
        mb.check_and_reserve(tenant_id="t1", user_id="u1")
        snap = mb.snapshot()
        assert "tenants" in snap
        assert "t1" in snap["tenants"]
        assert "users" in snap
