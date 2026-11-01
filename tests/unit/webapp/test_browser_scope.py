"""A stale browser tab must not send writes under another Cookie identity."""


def test_previous_tenant_header_rejects_write_after_cookie_switch(
    browser, register_user, csrf_headers
):
    original = register_user(browser)
    team = browser.post(
        "/v1/tenants", headers=csrf_headers(browser), json={"name": "Second space"}
    ).json()["data"]
    browser.post(f"/v1/tenants/{team['id']}/switch", headers=csrf_headers(browser))
    headers = {
        **csrf_headers(browser),
        "X-AGR-Tenant": original["tenant_id"],
        "X-AGR-User": original["user_id"],
    }
    result = browser.patch("/v1/me", headers=headers, json={"display_name": "Stale write"})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "IDENTITY_CHANGED"
    assert browser.get("/v1/me").json()["data"]["display_name"] == original["display_name"]


def test_stale_account_cannot_logout_the_new_account(browser, register_user, csrf_headers):
    old = register_user(browser)
    current = register_user(browser, "current@example.com")
    result = browser.post(
        "/v1/auth/logout",
        headers={
            **csrf_headers(browser),
            "X-AGR-User": old["user_id"],
            "X-AGR-Tenant": old["tenant_id"],
        },
    )
    assert result.status_code == 409
    assert browser.get("/v1/me").json()["data"]["user_id"] == current["user_id"]


def test_scoped_logout_with_expired_access_requires_refresh_before_revocation(
    browser, register_user, csrf_headers
):
    old = register_user(browser)
    current = register_user(browser, "current@example.com")
    browser.cookies.delete("agr_access")
    result = browser.post(
        "/v1/auth/logout",
        headers={**csrf_headers(browser), "X-AGR-User": old["user_id"]},
    )
    assert result.status_code == 401
    refresh = browser.post("/v1/auth/refresh", headers=csrf_headers(browser))
    assert refresh.status_code == 200
    assert refresh.json()["data"]["user_id"] == current["user_id"]


def test_browser_preconditions_do_not_change_service_key_identity(
    browser, register_user, csrf_headers
):
    current = register_user(browser)
    key = browser.post(
        f"/v1/tenants/{current['tenant_id']}/api-keys",
        headers=csrf_headers(browser),
        json={"name": "Service", "role": "reader"},
    ).json()["data"]["key"]
    response = browser.get(
        "/v1/me",
        headers={"Authorization": f"Bearer {key}", "X-AGR-User": "stale", "X-AGR-Tenant": "other"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["tenant_id"] == current["tenant_id"]
    assert response.json()["data"]["role"] == "reader"
