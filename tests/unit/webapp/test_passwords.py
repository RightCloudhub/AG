"""Reset tokens and password changes revoke all sessions and remain single-use."""

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("argon2")
pytest.importorskip("jwt")

from fastapi.testclient import TestClient


def test_forgot_response_is_uniform_and_reset_revokes_sessions(
    browser, web_app, mailbox, register_user, csrf_headers
):
    register_user(browser)
    old_session = TestClient(web_app)
    old_session.cookies.update(dict(browser.cookies))
    known = browser.post(
        "/auth/forgot", headers=csrf_headers(browser), json={"email": "person@example.com"}
    )
    unknown = browser.post(
        "/auth/forgot", headers=csrf_headers(browser), json={"email": "missing@example.com"}
    )
    assert known.json() == unknown.json()
    assert len(mailbox.messages) == 1
    token = mailbox.messages[0][2].split("token=")[1]
    response = browser.post(
        "/auth/reset",
        headers=csrf_headers(browser),
        json={"token": token, "password": "New-password-42!"},
    )
    assert response.status_code == 200
    assert old_session.get("/me").status_code == 401
    assert (
        browser.post(
            "/auth/reset",
            headers=csrf_headers(browser),
            json={"token": token, "password": "Another-password-42!"},
        ).status_code
        == 422
    )
    for password, status in [("Correct-horse-42!", 401), ("New-password-42!", 200)]:
        assert (
            browser.post(
                "/auth/login",
                headers=csrf_headers(browser),
                json={"email": "person@example.com", "password": password},
            ).status_code
            == status
        )


def test_profile_password_and_device_sessions(browser, web_app, register_user, csrf_headers):
    register_user(browser)
    peer = TestClient(web_app)
    peer.post(
        "/auth/login",
        headers=csrf_headers(peer),
        json={"email": "person@example.com", "password": "Correct-horse-42!"},
    )
    profile = browser.patch("/me", headers=csrf_headers(browser), json={"display_name": "New name"})
    assert profile.json()["data"]["display_name"] == "New name"
    devices = browser.get("/auth/sessions").json()["data"]
    assert len(devices) == 2 and sum(device["current"] for device in devices) == 1
    other = next(device for device in devices if not device["current"])
    assert (
        browser.delete(f"/auth/sessions/{other['id']}", headers=csrf_headers(browser)).status_code
        == 200
    )
    assert peer.get("/me").status_code == 401
    wrong = browser.post(
        "/auth/password/change",
        headers=csrf_headers(browser),
        json={"current_password": "wrong", "password": "New-password-42!"},
    )
    assert wrong.status_code == 422
    assert browser.get("/me").status_code == 200
    changed = browser.post(
        "/auth/password/change",
        headers=csrf_headers(browser),
        json={"current_password": "Correct-horse-42!", "password": "New-password-42!"},
    )
    assert changed.status_code == 200
    assert browser.get("/me").status_code == 401


def test_cannot_revoke_other_users_session(browser, web_app, register_user, csrf_headers):
    register_user(browser)
    private_id = browser.get("/auth/sessions").json()["data"][0]["id"]
    other = TestClient(web_app)
    register_user(other, "other@example.com")
    assert (
        other.delete(f"/auth/sessions/{private_id}", headers=csrf_headers(other)).status_code == 404
    )
    assert browser.get("/me").status_code == 200
