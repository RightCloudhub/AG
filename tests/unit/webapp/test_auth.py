"""Registration, login, Cookie security, refresh rotation and immediate revocation."""

import time

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("argon2")
pytest.importorskip("jwt")

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from agentic_graphrag.webapp.models import AuthSession, Membership, RefreshToken, Tenant, User


def test_registration_is_atomic_and_identity_has_existing_capabilities(
    browser, web_app, register_user, csrf_headers
):
    data = register_user(browser, "Person@Example.COM")
    assert data["email"] == "person@example.com"
    assert data["display_name"] == "Alex"
    assert data["role"] == "admin" and data["authenticated"]
    assert set(data["capabilities"]) >= {"chat", "graph", "knowledge", "review", "ops"}
    assert data["tenants"][0]["id"] == data["tenant_id"]
    assert browser.get("/me").json()["data"]["user_id"] == data["user_id"]
    duplicate = browser.post(
        "/auth/register",
        headers=csrf_headers(browser),
        json={
            "email": "person@example.com",
            "password": "Another-Strong-42!",
            "display_name": "Second",
        },
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["error"]["details"]["field_errors"] == {"email": "email_taken"}
    with web_app.state.identity.database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(select(func.count()).select_from(Tenant)) == 1
        assert session.scalar(select(func.count()).select_from(Membership)) == 1
        user = session.get(User, data["user_id"])
        assert user.password_hash.startswith("$argon2id$")
        refresh = session.scalar(select(RefreshToken))
        assert refresh.token_hash != browser.cookies["agr_refresh"]


def test_auth_cookies_are_httponly_and_csrf_is_required(browser, csrf_headers):
    body = {"email": "person@example.com", "password": "Correct-horse-42!", "display_name": "Alex"}
    assert browser.post("/auth/register", json=body).status_code == 403
    headers = csrf_headers(browser)
    denied = browser.post(
        "/auth/register", json=body, headers={**headers, "Origin": "https://evil.example"}
    )
    assert denied.status_code == 403
    response = browser.post("/auth/register", json=body, headers=headers)
    assert response.status_code == 201
    for cookie in response.headers.get_list("set-cookie"):
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "no-store" in response.headers["cache-control"]
    assert browser.get("/me", headers={"Authorization": "Bearer unknown"}).status_code == 401


def test_refresh_rotates_and_replay_revokes_entire_session(
    browser, web_app, register_user, csrf_headers
):
    register_user(browser)
    old_cookies = dict(browser.cookies)
    old_headers = csrf_headers(browser)
    old_cookies = dict(browser.cookies)
    response = browser.post("/auth/refresh", headers=old_headers)
    assert response.status_code == 200
    assert browser.cookies["agr_refresh"] != old_cookies["agr_refresh"]
    assert browser.cookies["agr_access"] != old_cookies["agr_access"]
    current_cookies = dict(browser.cookies)
    replay = TestClient(web_app)
    replay.cookies.update(old_cookies)
    assert replay.get("/me").status_code == 401
    assert replay.post("/auth/refresh", headers=old_headers).status_code == 401
    browser.cookies.clear()
    browser.cookies.update(current_cookies)
    assert browser.get("/me").status_code == 401


def test_logout_invalidates_saved_access_and_refresh(browser, web_app, register_user, csrf_headers):
    register_user(browser)
    saved = list(browser.cookies.jar)
    assert browser.post("/auth/logout", headers=csrf_headers(browser)).status_code == 200
    assert not browser.cookies
    for cookie in saved:
        browser.cookies.jar.set_cookie(cookie)
    assert browser.get("/me").status_code == 401
    assert browser.post("/auth/refresh", headers=csrf_headers(browser)).status_code == 401


def test_csrf_bootstrap_is_stable_for_tabs_sharing_a_session(browser, register_user, csrf_headers):
    register_user(browser)
    first = csrf_headers(browser)
    second = csrf_headers(browser)
    assert first == second
    assert (
        browser.patch("/me", headers=first, json={"display_name": "Same session"}).status_code
        == 200
    )


def test_logout_revokes_access_even_without_refresh_cookie(
    browser, web_app, register_user, csrf_headers
):
    register_user(browser)
    saved = TestClient(web_app)
    saved.cookies.update(dict(browser.cookies))
    browser.cookies.delete("agr_refresh")
    assert browser.post("/auth/logout", headers=csrf_headers(browser)).status_code == 200
    assert saved.get("/me").status_code == 401


def test_login_and_expired_access_refresh(browser, web_app, register_user, csrf_headers):
    register_user(browser)
    browser.post("/auth/logout", headers=csrf_headers(browser))
    bad = browser.post(
        "/auth/login",
        headers=csrf_headers(browser),
        json={
            "email": "person@example.com",
            "password": "wrong",
        },
    )
    assert bad.status_code == 401
    good = browser.post(
        "/auth/login",
        headers=csrf_headers(browser),
        json={
            "email": "PERSON@example.com",
            "password": "Correct-horse-42!",
        },
    )
    assert good.status_code == 200
    browser.cookies.delete("agr_access")
    assert browser.get("/me").status_code == 401
    assert browser.post("/auth/refresh", headers=csrf_headers(browser)).status_code == 200
    with web_app.state.identity.database.transaction(write=True) as session:
        for record in session.scalars(select(AuthSession)):
            record.expires_at = time.time() - 1
    assert browser.get("/me").status_code == 401
    assert browser.post("/auth/refresh", headers=csrf_headers(browser)).status_code == 401


@pytest.mark.parametrize(
    "field,value", [("email", "bad-email"), ("password", "short"), ("display_name", " ")]
)
def test_registration_field_errors_do_not_echo_passwords(browser, csrf_headers, field, value):
    body = {
        "email": "person@example.com",
        "password": "Correct-horse-42!",
        "display_name": "Alex",
        field: value,
    }
    response = browser.post("/auth/register", headers=csrf_headers(browser), json=body)
    assert response.status_code == 422
    assert field in response.json()["error"]["details"]["field_errors"]
    assert "Correct-horse-42!" not in response.text
