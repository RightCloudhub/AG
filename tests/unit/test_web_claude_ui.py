"""Structural + smoke tests for trial web UI (P4-UI-01 / P5-UI-01).

Covers: file presence, HTML structure with Vue root + v-cloak, JS endpoint
constants, SSE event names, injection safety (no v-html / innerHTML),
chain-view.js exports, and the real API query/feedback/stream paths.

HTTP_OK is a named constant; file <= 300 lines.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.config import ROOT_DIR

HTTP_OK = 200


def _web_files() -> dict[str, str]:
    web = ROOT_DIR / "web"
    return {
        "html": (web / "index.html").read_text(encoding="utf-8"),
        "app_css": (web / "static" / "app.css").read_text(encoding="utf-8"),
        "chat_css": (web / "static" / "chat.css").read_text(encoding="utf-8"),
        "panels_css": (web / "static" / "panels.css").read_text(encoding="utf-8"),
        "app_js": (web / "static" / "app.js").read_text(encoding="utf-8"),
        "api_js": (web / "static" / "js" / "api.js").read_text(encoding="utf-8"),
        "chain_js": (web / "static" / "js" / "chain-view.js").read_text(encoding="utf-8"),
        "root_js": (web / "static" / "js" / "root.js").read_text(encoding="utf-8"),
        "components_index": (
            web / "static" / "js" / "components" / "index.js"
        ).read_text(encoding="utf-8"),
        "components_widgets": (
            web / "static" / "js" / "components" / "widgets.js"
        ).read_text(encoding="utf-8"),
        "components_answer": (
            web / "static" / "js" / "components" / "answer-turn.js"
        ).read_text(encoding="utf-8"),
    }


def test_all_web_files_exist():
    """Every file referenced by the Vue 3 boot chain must be present."""
    files = _web_files()
    for name, content in files.items():
        assert content, f"file {name} is empty or missing"


def test_html_vue_root_structure():
    html = _web_files()["html"]
    assert 'id="app"' in html, "Vue root mount point"
    assert "v-cloak" in html, "v-cloak attribute on dynamic content"
    assert 'type="module"' in html, "app.js loaded as ES module"
    # Three CSS links
    assert "/web/static/app.css" in html
    assert "/web/static/chat.css" in html
    assert "/web/static/panels.css" in html
    # Composer + query input
    assert 'id="q"' in html
    assert 'id="askForm"' in html
    # Vue component tags
    assert "answer-turn" in html
    assert "progress-log" in html


def test_css_tokens_light_warm():
    css = _web_files()["app_css"]
    assert "--bg:" in css
    assert "#f5f2eb" in css
    assert "--accent:" in css
    # No dark primary bg
    assert "--bg: #0f1419" not in css


def test_app_js_vue_version_and_vendor_priority():
    js = _web_files()["app_js"]
    assert "3.5.13" in js, "Pinned Vue version"
    assert "vendor/vue.esm-browser.prod.js" in js, "Vendor path"
    # Vendor must be first source
    vendor_idx = js.index("vendor/vue.esm-browser.prod.js")
    cdn_idx = js.index("cdn.jsdelivr")
    assert vendor_idx < cdn_idx, "Vendor must be checked before CDN"


def test_api_js_endpoints_and_sse_events():
    js = _web_files()["api_js"]
    assert "/v1/query" in js
    assert "/v1/query/stream" in js
    assert "/v1/feedback" in js
    assert "/healthz" in js
    assert "event:" in js
    assert "data:" in js
    assert "fetch(" in js


def test_injection_safety_no_vhtml_or_innerhtml():
    """All front-end dynamic text must use mustache / textContent only.
    Zero hits for v-html and innerHTML across every web file."""
    files = _web_files()
    for name, content in files.items():
        assert "v-html" not in content, f"v-html found in {name}"
        assert "innerHTML" not in content, f"innerHTML found in {name}"


def test_chain_view_exports():
    js = _web_files()["chain_js"]
    assert "export function buildAnswerSegments" in js
    assert "export function buildPlanNodes" in js
    assert "export function parsePath" in js
    assert "export function describeStreamEvent" in js
    assert "MAX_PATH_ROWS" in js


def test_components_index_registers():
    js = _web_files()["components_index"]
    assert "registerComponents" in js
    assert "progress-log" in js
    assert "answer-turn" in js


def test_get_web_returns_vue_html():
    svc = QueryService.create_offline()
    app = create_app(query_service=svc)
    client = TestClient(app)
    r = client.get("/web")
    assert r.status_code == HTTP_OK
    body = r.text
    assert 'id="app"' in body
    # static resources served
    assert client.get("/web/static/js/api.js").status_code == HTTP_OK
    assert client.get("/web/static/chat.css").status_code == HTTP_OK
    svc.close()


def test_web_query_and_feedback_path_still_works():
    """Drive the real API endpoints the UI calls."""
    svc = QueryService.create_offline()
    app = create_app(query_service=svc)
    client = TestClient(app)

    r = client.post(
        "/v1/query",
        json={"question": "Who is the CEO of Apex Holdings?"},
    )
    assert r.status_code == HTTP_OK
    env = r.json()
    assert env["success"] is True
    data = env["data"]
    assert data["answer"]
    assert data["query_id"]

    fb = client.post(
        "/v1/feedback",
        json={"query_id": data["query_id"], "accurate": True, "reason": "ui-test"},
    )
    assert fb.status_code == HTTP_OK
    assert fb.json()["success"] is True

    with client.stream(
        "POST",
        "/v1/query/stream",
        json={"question": "Who is the CEO of Apex Holdings?"},
    ) as resp:
        assert resp.status_code == HTTP_OK
        text = "".join(resp.iter_text())
    assert "event:" in text
    assert "answer" in text
    svc.close()
