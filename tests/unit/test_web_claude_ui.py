"""Structural + smoke tests for Vue 3 zero-build trial UI (P5-UI-01 / ADR-006)."""

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
    STATIC / "tokens.css",
    STATIC / "rail.css",
    STATIC / "shell.css",
    STATIC / "controls.css",
    STATIC / "primitives.css",
    STATIC / "overlays.css",
    STATIC / "chat.css",
    STATIC / "composer.css",
    STATIC / "panels.css",
    STATIC / "reasoning.css",
    STATIC / "console.css",
    STATIC / "app.js",
    STATIC / "js" / "api.js",
    STATIC / "js" / "chain-view.js",
    STATIC / "js" / "root.js",
    STATIC / "js" / "components" / "index.js",
    STATIC / "js" / "components" / "widgets.js",
    STATIC / "js" / "components" / "answer-turn.js",
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
    """index.html is the mount shell only (p5-ui-02 U-05); chat markup moved
    to views/chat.js and is asserted in test_chat_view_template_structure."""
    html = _read(WEB / "index.html")
    assert 'id="app"' in html
    assert "v-cloak" in html
    assert 'type="module"' in html
    # Stylesheet modules (P5-UI-03 structural pass): tokens → base → views.
    for css in (
        "tokens.css",
        "rail.css",
        "shell.css",
        "controls.css",
        "primitives.css",
        "overlays.css",
        "chat.css",
        "composer.css",
        "panels.css",
        "reasoning.css",
        "console.css",
    ):
        assert f"/web/static/{css}" in html
    assert "/web/static/app.js" in html


def test_chat_view_template_structure():
    """Chat markup (ids/classes the old in-DOM template carried) now lives in
    the chat view component template (U-12 test split)."""
    src = _read(STATIC / "js" / "views" / "chat.js")
    assert 'id="q"' in src
    assert 'id="askForm"' in src
    assert "answer-turn" in src
    assert "progress-log" in src
    assert "thinking-panel" in src
    assert 'id="forceAgentic"' in src
    assert 'id="maxHops"' in src
    assert 'id="useStream"' in src


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
    assert '"/v1/query"' in api or "'/v1/query'" in api
    assert "/v1/query/stream" in api
    assert "/v1/feedback" in api
    assert "/healthz" in api
    assert "fetch(" in api
    chain = _read(STATIC / "js" / "chain-view.js")
    for name in CHAIN_EXPORTS:
        assert f"export function {name}" in chain or f"function {name}" in chain
    chat = _read(STATIC / "js" / "views" / "chat.js")
    for evt in SSE_EVENTS:
        # answer/error handled in chat view; progress events listed in chain-view
        assert evt in chain or evt in chat


def test_injection_safety_no_vhtml_or_innerhtml():
    """Mustache/textContent only — no Vue v-html directive or .innerHTML writes."""
    text = _all_frontend_text()
    assert "v-html" not in text
    assert ".innerHTML" not in text


def test_css_tokens_and_new_classes():
    """CSS module map (P5-UI-03 structural pass): tokens → rail/shell →
    controls/primitives/overlays → chat/composer/panels/reasoning/console."""
    tokens_css = _read(STATIC / "tokens.css")
    rail_css = _read(STATIC / "rail.css")
    shell_css = _read(STATIC / "shell.css")
    controls_css = _read(STATIC / "controls.css")
    primitives_css = _read(STATIC / "primitives.css")
    composer_css = _read(STATIC / "composer.css")
    panels_css = _read(STATIC / "panels.css")
    reasoning_css = _read(STATIC / "reasoning.css")
    # Dual-theme token system (P5-UI-03): dark is the :root default, light the
    # html[data-theme="light"] override; both palettes must ship in tokens.css.
    assert "--bg:" in tokens_css
    assert "#09090b" in tokens_css  # dark bg
    assert "#fafafa" in tokens_css  # light bg
    assert 'data-theme="light"' in tokens_css
    assert "--warn" in tokens_css
    assert "--avatar-w" in tokens_css
    assert "[v-cloak]" in tokens_css
    assert ".rail-health" in rail_css
    assert ".health-dot" in rail_css
    assert ".boot-error" in shell_css
    assert ".badge" in controls_css
    assert ".mini-btn" in controls_css
    assert ".skeleton" in controls_css
    assert ".stat-card" in primitives_css
    assert ".state-card" in primitives_css
    assert ".data-table" in primitives_css
    assert ".stop-btn" in composer_css
    assert ".progress-state" in reasoning_css
    assert ".progress-live" in reasoning_css
    assert ".thinking-card" in reasoning_css
    assert ".thinking-detail" in reasoning_css
    assert ".path-overflow" in reasoning_css
    assert ".claim-active" in panels_css
    assert ".feedback-note" in panels_css
    assert ".retry-row" in panels_css
    # Must not be the old dark primary background
    assert "--bg: #0f1419" not in tokens_css


def test_get_web_and_static_assets():
    svc = QueryService.create_offline()
    app = create_app(query_service=svc)
    client = TestClient(app)
    r = client.get("/web")
    assert r.status_code == HTTP_OK
    body = r.text
    assert 'id="app"' in body
    assert "claude-app" in body
    assert "/web/static/console.css" in body

    for path in (
        "/web/static/tokens.css",
        "/web/static/rail.css",
        "/web/static/shell.css",
        "/web/static/controls.css",
        "/web/static/primitives.css",
        "/web/static/overlays.css",
        "/web/static/chat.css",
        "/web/static/composer.css",
        "/web/static/panels.css",
        "/web/static/reasoning.css",
        "/web/static/console.css",
        "/web/static/app.js",
        "/web/static/js/api.js",
        "/web/static/js/chain-view.js",
        "/web/static/js/root.js",
        "/web/static/js/router.js",
        "/web/static/js/api-console.js",
        "/web/static/js/components/index.js",
        "/web/static/js/views/registry.js",
        "/web/static/js/views/chat.js",
    ):
        resp = client.get(path)
        assert resp.status_code == HTTP_OK, path
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
