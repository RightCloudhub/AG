"""Tenant boundaries, revision concurrency and a complete admin profile lifecycle."""

import pytest
from fastapi.testclient import TestClient
from test_model_roles import http_probe as http_probe
from test_model_roles import models as models

from agentic_graphrag.api.app import create_app
from agentic_graphrag.app.model_config import effective_model_config
from agentic_graphrag.app.model_profiles import ModelProfiles
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.core.models import ModelRole
from agentic_graphrag.llm.profile_store import ProfileConflict


def test_draft_test_publish_and_rollback_are_separate_persistent_actions(
    models, http_probe, tmp_path
):
    cfg, settings, profile = models
    path = tmp_path / "models.sqlite3"
    app = ModelProfiles(cfg, settings, path=path)
    first = app.save_draft(profile, expected_revision=0)
    with pytest.raises(KeyError):
        app.active(first.id, tenant_id="alpha")
    with pytest.raises(ProfileConflict, match="pass capability"):
        app.publish(
            first.id, tenant_id="alpha", revision=1, expected_active_revision=0, actor="admin"
        )
    assert app.test(first.id, tenant_id="alpha", revision=1).passed
    app.publish(first.id, tenant_id="alpha", revision=1, expected_active_revision=0, actor="admin")
    second = app.save_draft(first.model_copy(update={"name": "Second"}), expected_revision=1)
    assert second.revision == 2
    assert app.active(first.id, tenant_id="alpha").revision == 1
    with pytest.raises(ProfileConflict):
        app.save_draft(first, expected_revision=1)
    assert app.test(first.id, tenant_id="alpha", revision=2).passed
    app.publish(first.id, tenant_id="alpha", revision=2, expected_active_revision=1, actor="admin")
    app.publish(first.id, tenant_id="alpha", revision=1, expected_active_revision=2, actor="admin")
    reopened = ModelProfiles(cfg, settings, path=path)
    assert reopened.active(first.id, tenant_id="alpha").revision == 1
    assert reopened.store.get(first.id, tenant_id="alpha").revision == 2
    assert reopened.store.list(tenant_id="beta") == []
    with pytest.raises(KeyError):
        reopened.store.get(first.id, tenant_id="beta")


def test_embedding_change_requires_a_shadow_index(models, http_probe, tmp_path):
    cfg, settings, profile = models
    cfg.models.catalog["embedding-v2"] = cfg.models.catalog["embedding"].model_copy(
        update={"revision": "2"}
    )
    app = ModelProfiles(cfg, settings, path=tmp_path / "models.sqlite3")
    first = app.save_draft(profile, expected_revision=0)
    app.test(first.id, tenant_id="alpha", revision=1)
    app.publish(first.id, tenant_id="alpha", revision=1, expected_active_revision=0, actor="admin")
    roles = {**first.roles, ModelRole.EMBEDDING: "embedding-v2"}
    app.save_draft(first.model_copy(update={"roles": roles}), expected_revision=1)
    app.test(first.id, tenant_id="alpha", revision=2)
    with pytest.raises(ProfileConflict, match="KB reindex"):
        app.publish(
            first.id, tenant_id="alpha", revision=2, expected_active_revision=1, actor="admin"
        )
    assert app.active(first.id, tenant_id="alpha").revision == 1


def test_effective_profile_does_not_mutate_global_config(models, http_probe, tmp_path, monkeypatch):
    cfg, settings, profile = models
    monkeypatch.setenv("AGR_RUNTIME_DIR", str(tmp_path))
    app = ModelProfiles(cfg, settings)
    app.save_draft(profile, expected_revision=0)
    app.test(profile.id, tenant_id="alpha", revision=1)
    app.publish(
        profile.id, tenant_id="alpha", revision=1, expected_active_revision=0, actor="admin"
    )
    effective = effective_model_config(
        cfg, settings, tenant_id="alpha", kb_id="contracts", profile_id=profile.id
    )
    assert cfg.models.profile_revision == "legacy"
    assert effective.models.profile_revision == "balanced:1"
    assert effective.llm.embedding_model.startswith("agemb:")
    assert cfg.llm.embedding_model == "text-embedding-3-small"
    with pytest.raises(KeyError):
        effective_model_config(
            cfg, settings, tenant_id="beta", kb_id="contracts", profile_id=profile.id
        )


def test_admin_api_cannot_override_tenant_endpoint_or_secrets(
    models, http_probe, tmp_path, monkeypatch
):
    cfg, settings, profile = models
    monkeypatch.setenv("AGR_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("AGR_REQUIRE_AUTH", "1")
    monkeypatch.setenv("AGR_API_KEYS", "alpha:admin:admin,alpha:reader:reader,beta:other:admin")
    svc = QueryService.create_offline(cfg=cfg, settings=settings)
    client = TestClient(create_app(query_service=svc))
    headers = {"Authorization": "Bearer admin"}
    body = {"id": profile.id, "name": profile.name, "roles": profile.roles}
    assert client.post("/v1/model-profiles", json=body).status_code == 401
    assert (
        client.post(
            "/v1/model-profiles", json=body, headers={"Authorization": "Bearer reader"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/model-profiles", json={**body, "tenant_id": "beta"}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/model-profiles", json={**body, "base_url": "http://internal"}, headers=headers
        ).status_code
        == 422
    )
    created = client.post("/v1/model-profiles", json=body, headers=headers)
    assert created.status_code == 200 and created.json()["data"]["revision"] == 1
    catalog = client.get("/v1/model-profiles/catalog", headers=headers)
    assert "secret_ref" not in catalog.text and "base_url" not in catalog.text
    other = {"Authorization": "Bearer other"}
    assert client.get("/v1/model-profiles", headers=other).json()["data"] == []
    assert (
        client.post(
            "/v1/model-profiles/balanced/test", json={"revision": 1}, headers=other
        ).status_code
        == 404
    )
    assert client.post(
        "/v1/model-profiles/balanced/test", json={"revision": 1}, headers=headers
    ).json()["data"]["passed"]
    assert (
        client.post(
            "/v1/model-profiles/balanced/publish",
            json={
                "revision": 1,
                "expected_active_revision": 0,
            },
            headers=headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/v1/model-profiles/balanced/publish",
            json={
                "revision": 1,
                "expected_active_revision": 0,
            },
            headers=headers,
        ).status_code
        == 409
    )
    svc.close()


def test_failed_tests_never_activate_and_do_not_expose_upstream_body(models, tmp_path, monkeypatch):
    import httpx

    from agentic_graphrag.llm.http_transport import ProviderHttpPool

    cfg, settings, profile = models

    def forbidden(*_a, **_k):
        req = httpx.Request("POST", "http://private-endpoint/v1")
        response = httpx.Response(401, request=req, text="secret upstream response")
        raise httpx.HTTPStatusError("secret upstream response", request=req, response=response)

    monkeypatch.setattr(ProviderHttpPool, "post", forbidden)
    app = ModelProfiles(cfg, settings, path=tmp_path / "models.sqlite3")
    app.save_draft(profile, expected_revision=0)
    report = app.test(profile.id, tenant_id="alpha", revision=1)
    assert not report.passed
    assert "secret" not in report.model_dump_json()
    assert any(row.error_code == "authentication" for row in report.checks)
    with pytest.raises(ProfileConflict):
        app.publish(
            profile.id, tenant_id="alpha", revision=1, expected_active_revision=0, actor="a"
        )
