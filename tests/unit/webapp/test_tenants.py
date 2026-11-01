"""Tenant switching, roles and concurrent last-administrator protection."""

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import select

from agentic_graphrag.api.auth import Principal
from agentic_graphrag.api.rbac import Role
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.webapp.models import Membership
from agentic_graphrag.webapp.tenants import change_member


def test_create_switch_rename_and_url_scope(browser, register_user, csrf_headers):
    me = register_user(browser)
    old_access = browser.cookies.get("agr_access")
    response = browser.post("/tenants", json={"name": " Research "}, headers=csrf_headers(browser))
    assert response.status_code == 201
    tenant = response.json()["data"]
    assert tenant["name"] == "Research"
    assert tenant["member_count"] == 1
    assert len(browser.get("/v1/tenants").json()["data"]) == 2
    assert browser.get(f"/v1/tenants/{tenant['id']}").status_code == 404
    response = browser.post(f"/tenants/{tenant['id']}/switch", headers=csrf_headers(browser))
    assert response.status_code == 200
    assert response.json()["data"]["tenant_id"] == tenant["id"]
    assert browser.cookies.get("agr_access") != old_access
    assert browser.get(f"/v1/tenants/{me['tenant_id']}").status_code == 404
    renamed = browser.patch(
        f"/v1/tenants/{tenant['id']}", json={"name": "Lab"}, headers=csrf_headers(browser)
    )
    assert renamed.json()["data"]["name"] == "Lab"
    assert (
        browser.post("/v1/tenants/missing/switch", headers=csrf_headers(browser)).status_code == 404
    )


def test_roles_cannot_manage_members_or_remove_last_admin(
    browser, web_app, register_user, csrf_headers
):
    me = register_user(browser)
    base = f"/v1/tenants/{me['tenant_id']}"
    assert (
        browser.patch(
            f"{base}/members/{me['user_id']}",
            json={"role": "reader"},
            headers=csrf_headers(browser),
        ).status_code
        == 409
    )
    assert (
        browser.delete(f"{base}/members/{me['user_id']}", headers=csrf_headers(browser)).status_code
        == 409
    )
    peer = TestClient(web_app)
    other = register_user(peer, "reader@example.com")
    with web_app.state.identity.database.transaction(write=True) as session:
        session.add(Membership(user_id=other["user_id"], tenant_id=me["tenant_id"], role="reader"))
    assert peer.post(f"{base}/switch", headers=csrf_headers(peer)).status_code == 200
    assert peer.get(f"{base}/members").status_code == 403
    assert peer.get("/v1/tenants/missing/members").status_code == 404
    assert peer.get("/v1/me").json()["data"]["capabilities"]["settings_members"] is False
    assert (
        browser.patch(
            f"{base}/members/{other['user_id']}",
            json={"role": "operator"},
            headers=csrf_headers(browser),
        ).status_code
        == 200
    )
    assert peer.get("/v1/me").json()["data"]["role"] == "operator"
    assert (
        browser.delete(
            f"{base}/members/{other['user_id']}", headers=csrf_headers(browser)
        ).status_code
        == 200
    )
    assert peer.get("/v1/me").status_code == 401


def test_concurrent_self_demotions_keep_one_admin(browser, web_app, register_user):
    me = register_user(browser)
    peer = TestClient(web_app)
    other = register_user(peer, "second@example.com")
    db = web_app.state.identity.database
    with db.transaction(write=True) as session:
        session.add(Membership(user_id=other["user_id"], tenant_id=me["tenant_id"], role="admin"))

    def demote(user):
        current = Principal(
            tenant_id=me["tenant_id"],
            user_id=user["user_id"],
            api_key="",
            role=Role.ADMIN,
            session_id="test-session",
        )
        try:
            change_member(
                db, current, tenant_id=me["tenant_id"], user_id=user["user_id"], role="reader"
            )
        except ApiError as error:
            return error.code
        return "changed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(demote, [me, other]))
    assert sorted(results) == ["LAST_ADMIN", "changed"]
    with db.transaction() as session:
        roles = session.scalars(
            select(Membership.role).where(Membership.tenant_id == me["tenant_id"])
        ).all()
    assert sorted(roles) == ["admin", "reader"]
