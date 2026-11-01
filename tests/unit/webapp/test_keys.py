"""API keys are tenant-fixed, write-only secrets and immediately revocable."""

import time

from fastapi.testclient import TestClient

from agentic_graphrag.webapp.models import ApiKey


def test_key_once_only_role_scope_and_revocation(browser, web_app, register_user, csrf_headers):
    me = register_user(browser)
    base = f"/v1/tenants/{me['tenant_id']}/api-keys"
    created = browser.post(
        base, json={"name": "automation", "role": "reader"}, headers=csrf_headers(browser)
    )
    assert created.status_code == 201
    key = created.json()["data"]
    raw = key["key"]
    assert raw not in browser.get(base).text
    with web_app.state.identity.database.transaction() as session:
        stored = session.get(ApiKey, key["id"])
        assert stored.key_hash != raw and len(stored.key_hash) == 64
    client = TestClient(web_app, headers={"X-API-Key": raw})
    assert client.get("/v1/me").json()["data"]["tenant_id"] == me["tenant_id"]
    assert client.get(f"/v1/tenants/{me['tenant_id']}/members").status_code == 403
    assert client.get("/v1/tenants/other/members").status_code == 404
    assert client.post(f"/v1/tenants/{me['tenant_id']}/switch").status_code == 401
    assert client.patch("/v1/me", json={"display_name": "Wrong"}).status_code == 401
    assert browser.delete(f"{base}/{key['id']}", headers=csrf_headers(browser)).status_code == 200
    assert client.get("/v1/me").status_code == 401


def test_admin_service_key_can_manage_own_tenant_only(
    browser, web_app, register_user, csrf_headers, monkeypatch
):
    me = register_user(browser)
    monkeypatch.setenv("AGR_API_KEYS", f"{me['tenant_id']}:service-admin:admin")
    client = TestClient(web_app, headers={"Authorization": "Bearer service-admin"})
    base = f"/v1/tenants/{me['tenant_id']}"
    assert client.get(f"{base}/members").status_code == 200
    response = client.post(f"{base}/api-keys", json={"name": "worker", "role": "operator"})
    assert response.status_code == 201
    assert client.post("/v1/tenants/other/api-keys", json={"name": "invalid"}).status_code == 404
    assert client.post("/v1/tenants", json={"name": "invalid"}).status_code == 401
    assert client.get("/v1/auth/sessions").status_code == 401
    expired = browser.post(
        f"{base}/api-keys",
        json={"name": "expired", "expires_at": time.time() - 1},
        headers=csrf_headers(browser),
    )
    assert expired.status_code == 422
