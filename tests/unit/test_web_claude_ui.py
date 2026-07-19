"""Structural + smoke tests for Claude-style trial web UI (P4-UI-01)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.config import ROOT_DIR


def _web_files() -> dict[str, str]:
    web = ROOT_DIR / "web"
    return {
        "html": (web / "index.html").read_text(encoding="utf-8"),
        "css": (web / "static" / "app.css").read_text(encoding="utf-8"),
        "js": (web / "static" / "app.js").read_text(encoding="utf-8"),
    }


def test_claude_style_css_tokens_are_light_warm():
    css = _web_files()["css"]
    # Light warm canvas (not dark dashboard)
    assert "--bg:" in css
    assert "#f5f2eb" in css or "#faf8f4" in css or "#faf9f5" in css
    assert "--accent:" in css
    # Must not be the old dark primary background
    assert "--bg: #0f1419" not in css
    assert "claude-app" in css or ".shell" in css


def test_html_chat_layout_structure():
    html = _web_files()["html"]
    assert 'class="claude-app"' in html or "claude-app" in html
    assert 'id="q"' in html
    assert 'id="askBtn"' in html or 'id="askForm"' in html
    assert 'id="answerBox"' in html
    assert 'id="progressList"' in html
    assert 'id="chainBox"' in html
    assert 'id="stepsBox"' in html
    assert 'id="fbReason"' in html
    assert 'data-acc="1"' in html
    assert 'data-acc="0"' in html
    assert "/web/static/app.css" in html
    assert "/web/static/app.js" in html


def test_js_calls_real_backend_endpoints():
    js = _web_files()["js"]
    assert '"/v1/query"' in js or "'/v1/query'" in js
    assert "/v1/query/stream" in js
    assert "/v1/feedback" in js
    assert "fetch(" in js


def test_get_web_returns_claude_html():
    svc = QueryService.create_offline()
    app = create_app(query_service=svc)
    client = TestClient(app)
    r = client.get("/web")
    assert r.status_code == 200
    body = r.text
    assert "claude-app" in body or "composer" in body
    assert 'id="q"' in body
    assert "#f5f2eb" in _web_files()["css"] or "f5f2eb" in body or "claude" in body.lower()
    # static css served
    css = client.get("/web/static/app.css")
    assert css.status_code == 200
    assert "background" in css.text.lower() or "--bg" in css.text
    js = client.get("/web/static/app.js")
    assert js.status_code == 200
    assert "/v1/query" in js.text
    svc.close()


def test_web_query_and_feedback_path_still_works():
    """Drive real API the UI uses (query + feedback)."""
    svc = QueryService.create_offline()
    app = create_app(query_service=svc)
    client = TestClient(app)

    r = client.post(
        "/v1/query",
        json={"question": "Who is the CEO of Apex Holdings?"},
    )
    assert r.status_code == 200
    env = r.json()
    assert env["success"] is True
    data = env["data"]
    assert data["answer"]
    assert data["query_id"]

    fb = client.post(
        "/v1/feedback",
        json={"query_id": data["query_id"], "accurate": True, "reason": "ui-test"},
    )
    assert fb.status_code == 200
    assert fb.json()["success"] is True

    with client.stream(
        "POST",
        "/v1/query/stream",
        json={"question": "Who is the CEO of Apex Holdings?"},
    ) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    assert "event:" in text
    assert "answer" in text
    svc.close()
