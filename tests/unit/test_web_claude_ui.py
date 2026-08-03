"""Structural + smoke tests for Vue 3 zero-build trial UI (P5-UI-01/02 / ADR-006)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.config import ROOT_DIR

HTTP_OK = 200
VUE_PIN = "vue@3.5.13"
WEB = ROOT_DIR / "web"
STATIC = WEB / "static"

REQUIRED_FILES = (
    WEB / "index.html",
    STATIC / "app.css",
    STATIC / "chat.css",
    STATIC / "panels.css",
    STATIC / "css" / "shell.css",
    STATIC / "css" / "views.css",
    STATIC / "app.js",
    STATIC / "js" / "api.js",
    STATIC / "js" / "chain-view.js",
    STATIC / "js" / "router.js",
    STATIC / "js" / "nav.js",
    STATIC / "js" / "views" / "chat.js",
    STATIC / "js" / "views" / "graph.js",
    STATIC / "js" / "views" / "dashboard.js",
    STATIC / "js" / "views" / "review.js",
    STATIC / "js" / "views" / "history.js",
    STATIC / "js" / "components" / "index.js",
    STATIC / "js" / "components" / "widgets.js",
    STATIC / "js" / "components" / "answer-turn.js",
    STATIC / "js" / "components" / "layout.js",
    STATIC / "vendor" / "README.md",
)

SSE_EVENTS = (
    "cache_hit",
    "triage",
    "thinking",
    "sub_question",
    "hop_done",
    "answer",
    "error",
)
CHAIN_EXPORTS = (
    "buildAnswerSegments",
    "buildPlanNodes",
    "parsePath",
    "describeStreamEvent",
    "describeThinkingEvent",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _all_frontend_text() -> str:
    """Project-authored frontend sources only (exclude vendored Vue runtime)."""
    chunks: list[str] = []
    vendor = STATIC / "vendor"
    for path in WEB.rglob("*"):
        if not path.is_file() or path.suffix not in {".html", ".js", ".css", ".md"}:
            continue
        if vendor in path.parents or path.parent == vendor:
            # Skip vue.esm-browser.prod.js; keep vendor/README.md for policy notes.
            if path.name != "README.md":
                continue
        chunks.append(_read(path))
    return "\n".join(chunks)


def test_required_web_files_exist():
    missing = [str(p.relative_to(ROOT_DIR)) for p in REQUIRED_FILES if not p.is_file()]
    assert not missing, f"missing web files: {missing}"


def test_html_vue_shell_structure():
    html = _read(WEB / "index.html")
    assert 'id="app"' in html
    assert "v-cloak" in html
    assert 'type="module"' in html
    for css in (
        "/web/static/app.css",
        "/web/static/chat.css",
        "/web/static/panels.css",
        "/web/static/css/shell.css",
        "/web/static/css/views.css",
    ):
        assert css in html
    assert "/web/static/app.js" in html
    # SPA shell: single mount, routing owned by the layout component.
    assert "<app-shell>" in html


def test_chat_view_keeps_chat_surface():
    """The old in-DOM chat template moved to the chat view component."""
    chat = _read(STATIC / "js" / "views" / "chat.js")
    for marker in (
        'id="q"',
        'id="askForm"',
        'id="forceAgentic"',
        'id="maxHops"',
        'id="useStream"',
        "answer-turn",
        "progress-log",
        "thinking-panel",
    ):
        assert marker in chat


def test_answer_turn_retry_label_depends_on_force_agentic():
    """Unforced turns offer force-agentic retry; forced turns only show re-ask."""
    src = _read(STATIC / "js" / "components" / "answer-turn.js")
    assert 'RETRY_FORCE_LABEL = "强制 Agentic 重问"' in src
    assert 'RETRY_AGAIN_LABEL = "再问一次"' in src
    assert "alreadyForceAgentic" in src
    assert "retryLabel" in src
    assert "{{ retryLabel }}" in src


def test_app_js_pins_vue_vendor_first():
    js = _read(STATIC / "app.js")
    assert 'VUE_VERSION = "3.5.13"' in js
    # Runtime URL is built as `vue@${VUE_VERSION}` — pin constant must be present.
    assert "VUE_VERSION" in js
    assert "vue@" in js or VUE_PIN.split("@")[0] in js
    vendor = "/web/static/vendor/vue.esm-browser.prod.js"
    assert vendor in js
    vendor_pos = js.index(vendor)
    cdn_pos = js.index("cdn.jsdelivr.net")
    unpkg_pos = js.index("unpkg.com")
    assert vendor_pos < cdn_pos < unpkg_pos
    assert "registerComponents" in js
    assert "createApp" in js


def test_js_backend_endpoints_and_sse_events():
    api = _read(STATIC / "js" / "api.js")
    for url in (
        '"/v1/query"',
        "'/v1/query'",
        "/v1/query/stream",
        "/v1/feedback",
        "/healthz",
        "/v1/graph/relations",
        "/v1/metrics",
        "/v1/budget/snapshot",
        "/v1/review-queue",
        "/v1/audit/queries/recent",
    ):
        assert url in api
    assert "fetch(" in api
    chain = _read(STATIC / "js" / "chain-view.js")
    for name in CHAIN_EXPORTS:
        assert f"export function {name}" in chain or f"function {name}" in chain
    chat = _read(STATIC / "js" / "views" / "chat.js")
    for evt in SSE_EVENTS:
        # progress events listed in chain-view; answer/error handled in chat view
        assert evt in chain or evt in chat


def test_router_and_nav_helpers():
    router = _read(STATIC / "js" / "router.js")
    for fn in ("parseHash", "hashFor", "navigate"):
        assert fn in router
    nav = _read(STATIC / "js" / "nav.js")
    for name in ("chat", "graph", "dashboard", "review", "history"):
        assert name in nav


def test_injection_safety_no_vhtml_or_innerhtml():
    """Mustache/textContent only — no Vue v-html directive or .innerHTML writes."""
    text = _all_frontend_text()
    assert "v-html" not in text
    assert ".innerHTML" not in text


def test_css_tokens_and_new_classes():
    app_css = _read(STATIC / "app.css")
    chat_css = _read(STATIC / "chat.css")
    panels_css = _read(STATIC / "panels.css")
    shell_css = _read(STATIC / "css" / "shell.css")
    views_css = _read(STATIC / "css" / "views.css")
    assert "--bg:" in app_css and "#f5f2eb" in app_css
    assert "--warn" in app_css and "--avatar-w" in app_css
    assert "[v-cloak]" in app_css and ".rail-health" in app_css
    assert ".health-dot" in app_css and ".boot-error" in app_css
    for cls in (".stop-btn", ".progress-state", ".progress-live", ".thinking-card", ".thinking-detail"):
        assert cls in chat_css
    for cls in (".claim-active", ".mini-btn", ".feedback-note", ".retry-row", ".path-overflow"):
        assert cls in panels_css
    # SPA shell + view styles
    assert ".nav-item" in shell_css and ".chat-shell" in shell_css
    assert ".view-page" in views_css and ".stat-card" in views_css and ".data-table" in views_css
    # Must not be the old dark primary background
    assert "--bg: #0f1419" not in app_css


def test_get_web_and_static_assets():
    svc = QueryService.create_offline()
    app = create_app(query_service=svc)
    client = TestClient(app)
    r = client.get("/web")
    assert r.status_code == HTTP_OK
    body = r.text
    assert 'id="app"' in body
    assert "claude-app" in body
    assert "<app-shell>" in body

    for path in (
        "/web/static/app.css",
        "/web/static/chat.css",
        "/web/static/panels.css",
        "/web/static/css/shell.css",
        "/web/static/css/views.css",
        "/web/static/app.js",
        "/web/static/js/api.js",
        "/web/static/js/chain-view.js",
        "/web/static/js/router.js",
        "/web/static/js/nav.js",
        "/web/static/js/views/chat.js",
        "/web/static/js/views/graph.js",
        "/web/static/js/components/index.js",
    ):
        assert client.get(path).status_code == HTTP_OK, path
    svc.close()


def test_graph_browse_and_history_endpoints():
    """New endpoints powering the graph explorer + history views."""
    svc = QueryService.create_offline()
    app = create_app(query_service=svc)
    client = TestClient(app)

    rel = client.get("/v1/graph/relations")
    assert rel.status_code == HTTP_OK and rel.json()["success"] is True

    ent = client.get("/v1/graph/entities/Apex Holdings")
    assert ent.status_code == HTTP_OK
    assert ent.json()["data"]["name"] == "Apex Holdings"

    nb = client.get("/v1/graph/entities/Apex Holdings/neighbors")
    assert nb.status_code == HTTP_OK and nb.json()["success"] is True

    assert client.get("/v1/graph/entities/NoSuchEntity").status_code == 404

    # Seed a chain so the history endpoint has a row.
    q = client.post("/v1/query", json={"question": "Who is the CEO of Apex Holdings?"})
    assert q.status_code == HTTP_OK and q.json()["success"] is True

    hist = client.get("/v1/audit/queries/recent")
    assert hist.status_code == HTTP_OK
    data = hist.json()
    assert data["success"] is True
    assert any(row["question"] for row in data["data"])
    svc.close()


def test_web_query_feedback_and_stream_still_work():
    """Drive real API the UI uses (query + feedback + SSE)."""
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
    assert fb.status_code == HTTP_OK and fb.json()["success"] is True

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
