"""History fallback is restricted; runtime tests exercise actual browser modules offline."""

import shutil
import subprocess

import pytest

from agentic_graphrag.config import ROOT_DIR


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/login",
        "/register",
        "/forgot",
        "/reset",
        "/invite/example",
        "/app/chat/id",
        "/app/missing",
    ],
)
def test_history_routes_are_public_html(browser, path):
    response = browser.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "/web/static/js/product/main.js" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_static_and_api_misses_never_fall_back_to_html(browser, register_user):
    register_user(browser)
    for path in ("/v1/nonexistent", "/web/static/missing.js", "/auth/missing", "/other"):
        response = browser.get(path)
        assert response.status_code == 404
        assert "text/html" not in response.headers["content-type"]
    assert browser.get("/web", follow_redirects=False).headers["location"] == "/app/chat"


def test_router_and_vue_share_one_local_runtime(browser):
    response = browser.get("/web/static/vendor/vue-router.esm-browser.prod.js")
    assert response.status_code == 200
    assert "vue-router v4.5.1" in response.text[:150]
    assert 'from"./vue.esm-browser.prod.js"' in response.text
    assert 'from"vue"' not in response.text


def test_static_module_loading_does_not_exhaust_business_rate_limit(browser, register_user):
    register_user(browser)
    for _ in range(25):
        assert browser.get("/web/static/js/product/main.js").status_code == 200
    assert browser.get("/v1/me").status_code == 200


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for JS validation")
def test_product_runtime():
    result = subprocess.run(
        [
            "node",
            "--test",
            "tests/unit/webapp/frontend_runtime.mjs",
            "tests/unit/webapp/frontend_transport.mjs",
        ],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
