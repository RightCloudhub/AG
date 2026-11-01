"""Invite delivery, rotation, expiration and non-escalating acceptance."""

import re
import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from agentic_graphrag.webapp.models import Invite, Membership


def _token(mailbox):
    return re.search(r"/invite/(\S+)", mailbox.messages[-1][2]).group(1)


def test_invite_resend_accept_and_single_use(
    browser, web_app, register_user, csrf_headers, mailbox
):
    me = register_user(browser)
    base = f"/v1/tenants/{me['tenant_id']}/invites"
    response = browser.post(
        base,
        json={"email": "Invitee@example.com", "role": "operator"},
        headers=csrf_headers(browser),
    )
    assert response.status_code == 201
    first = _token(mailbox)
    invite_id = response.json()["data"]["id"]
    assert first not in response.text
    public = TestClient(web_app)
    preview = public.get(f"/v1/invites/{first}").json()["data"]
    assert preview["email"] == "invitee@example.com"
    assert (
        browser.post(f"/v1/invites/{first}/accept", headers=csrf_headers(browser)).status_code
        == 403
    )
    assert (
        browser.post(
            base, json={"email": "invitee@example.com"}, headers=csrf_headers(browser)
        ).status_code
        == 409
    )
    assert (
        browser.post(f"{base}/{invite_id}/resend", headers=csrf_headers(browser)).status_code == 200
    )
    token = _token(mailbox)
    assert token != first
    assert public.get(f"/invites/{first}").status_code == 404
    invitee = register_user(public, "invitee@example.com")
    accepted = public.post(f"/v1/invites/{token}/accept", headers=csrf_headers(public))
    assert accepted.status_code == 200
    assert accepted.json()["data"]["role"] == "operator"
    assert (
        public.post(f"/v1/invites/{token}/accept", headers=csrf_headers(public)).status_code == 404
    )
    assert (
        public.post(
            f"/v1/tenants/{me['tenant_id']}/switch", headers=csrf_headers(public)
        ).status_code
        == 200
    )
    assert public.get("/v1/me").json()["data"]["user_id"] == invitee["user_id"]
    with web_app.state.identity.database.transaction() as session:
        row = session.get(Invite, invite_id)
        assert row.token_hash != token and len(row.token_hash) == 64
        assert row.accepted_at is not None


def test_accept_does_not_upgrade_existing_membership(
    browser, web_app, register_user, csrf_headers, mailbox
):
    me = register_user(browser)
    base = f"/v1/tenants/{me['tenant_id']}/invites"
    assert (
        browser.post(
            base,
            json={"email": "member@example.com", "role": "admin"},
            headers=csrf_headers(browser),
        ).status_code
        == 201
    )
    peer = TestClient(web_app)
    other = register_user(peer, "member@example.com")
    with web_app.state.identity.database.transaction(write=True) as session:
        session.add(Membership(user_id=other["user_id"], tenant_id=me["tenant_id"], role="reader"))
    response = peer.post(f"/v1/invites/{_token(mailbox)}/accept", headers=csrf_headers(peer))
    assert response.json()["data"]["role"] == "reader"


def test_expired_revoked_and_cross_tenant_invites(
    browser, web_app, register_user, csrf_headers, mailbox
):
    me = register_user(browser)
    base = f"/v1/tenants/{me['tenant_id']}/invites"
    row = browser.post(
        base, json={"email": "lost@example.com"}, headers=csrf_headers(browser)
    ).json()["data"]
    token = _token(mailbox)
    with web_app.state.identity.database.transaction(write=True) as session:
        invite = session.scalar(select(Invite).where(Invite.id == row["id"]))
        invite.expires_at = time.time() - 1
    assert browser.get(f"/v1/invites/{token}").status_code == 404
    assert browser.get(base).json()["data"][0]["status"] == "expired"
    assert (
        browser.post(f"{base}/{row['id']}/resend", headers=csrf_headers(browser)).status_code == 200
    )
    token = _token(mailbox)
    assert (
        browser.delete(
            f"/v1/tenants/other/invites/{row['id']}", headers=csrf_headers(browser)
        ).status_code
        == 404
    )
    assert browser.delete(f"{base}/{row['id']}", headers=csrf_headers(browser)).status_code == 200
    assert browser.get(f"/v1/invites/{token}").status_code == 404
