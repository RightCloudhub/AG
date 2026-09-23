"""Structural and API smoke tests for the Vue knowledge workspace."""

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
    STATIC / "app.css",
    STATIC / "layout.css",
    STATIC / "controls.css",
    STATIC / "mobile.css",
    STATIC / "conversation.css",
    STATIC / "console.css",
    STATIC / "ops.css",
    STATIC / "chat.css",
    STATIC / "panels.css",
    STATIC / "app.js",
    STATIC / "js" / "api.js",
    STATIC / "js" / "chain-view.js",
    STATIC / "js" / "root.js",
    STATIC / "js" / "views" / "registry.js",
    STATIC / "js" / "views" / "chat.js",
    STATIC / "js" / "views" / "knowledge.js",
    STATIC / "js" / "views" / "review.js",
    STATIC / "js" / "views" / "graph.js",
    STATIC / "js" / "views" / "ops.js",
    STATIC / "js" / "components" / "index.js",
    STATIC / "js" / "components" / "widgets.js",
    STATIC / "js" / "components" / "answer-turn.js",
    STATIC / "vendor" / "README.md",
)
SSE_EVENTS = ("cache_hit", "triage", "thinking", "sub_question", "hop_done", "answer", "error")
CHAIN_EXPORTS = (
    "buildAnswerSegments",
    "buildPlanNodes",
    "parsePath",
    "describeStreamEvent",
    "describeThinkingEvent",
)
VIEW_FILES = ("chat.js", "knowledge.js", "review.js", "graph.js", "ops.js")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _frontend_sources() -> str:
    files = [*WEB.rglob("*.html"), *WEB.rglob("*.js"), *WEB.rglob("*.css")]
    return "\n".join(
        _read(path) for path in files if STATIC / "vendor" not in path.parents
    )


def test_required_workspace_files_and_size_budgets():
    missing = [str(path.relative_to(ROOT_DIR)) for path in REQUIRED_FILES if not path.is_file()]
    assert not missing, f"missing workspace files: {missing}"
    checked = [*WEB.rglob("*.html"), *WEB.rglob("*.js"), *WEB.rglob("*.css")]
    checked = [path for path in checked if STATIC / "vendor" not in path.parents]
    oversized = [f"{path.relative_to(ROOT_DIR)}: {len(_read(path).splitlines())}" for path in checked if len(_read(path).splitlines()) > 300]
    assert not oversized, f"frontend size budget exceeded: {oversized}"
    assert len(_read(STATIC / "tokens.css").splitlines()) <= 140


def test_html_shell_and_all_local_stylesheets():
    html = _read(WEB / "index.html")
    assert 'lang="zh-CN"' in html
    assert 'id="app"' in html and "v-cloak" in html
    assert 'type="module"' in html and "/web/static/app.js" in html
    for path in REQUIRED_FILES:
        if path.suffix == ".css":
            assert f"/web/static/{path.relative_to(STATIC).as_posix()}" in html
    assert "知识推理工作台" in html


def test_role_aware_views_and_api_contracts():
    registry = _read(STATIC / "js" / "views" / "registry.js")
    for view in ("chat", "knowledge", "review", "graph", "ops"):
        assert f'id: "{view}"' in registry
    assert "visibleViews" in registry and "capabilities" in registry
    root = _read(STATIC / "js" / "root.js")
    assert "fetchMe" in root and "canOpenView" in root
    assert "keep-alive" in root
    assert "identityFailure === 'auth'" in root
    assert "无法连接工作区" in root

    api = _read(STATIC / "js" / "api.js")
    for endpoint in (
        "/v1/me",
        "/v1/query",
        "/v1/query/stream",
        "/v1/feedback",
        "/v1/docs",
        "/v1/ingest-tasks",
        "/v1/review-queue",
        "/v1/graph/entities",
        "/v1/metrics",
        "/v1/budget/snapshot",
        "/v1/audit-events",
        "/v1/audit/queries/",
        "/v1/traces/",
    ):
        assert endpoint in api
    chat = _read(STATIC / "js" / "views" / "chat.js")
    for event in SSE_EVENTS:
        assert event in chat or event in _read(STATIC / "js" / "chain-view.js")


def test_vue_runtime_remains_pinned_and_local_first():
    js = _read(STATIC / "app.js")
    assert 'VUE_VERSION = "3.5.13"' in js
    vendor = "/web/static/vendor/vue.esm-browser.prod.js"
    assert vendor in js and js.index(vendor) < js.index("cdn.jsdelivr.net") < js.index("unpkg.com")
    assert "registerComponents" in js and "createApp" in js


def test_frontend_injection_and_external_asset_safety():
    text = _frontend_sources()
    assert "v-html" not in text
    assert ".innerHTML" not in text
    css = "\n".join(_read(path) for path in STATIC.glob("*.css"))
    assert "http://" not in css and "https://" not in css
    assert "prefers-reduced-motion" in css
    assert ":focus-visible" in css


def test_chat_and_answer_interactions_remain_available():
    chat = _read(STATIC / "js" / "views" / "chat.js")
    assert "stopStreaming" in chat and "retryAgentic" in chat
    assert "sendFeedback" in chat and "exportConversation" in chat
    answer = _read(STATIC / "js" / "components" / "answer-turn.js")
    assert 'RETRY_FORCE_LABEL = "强制 Agentic 重问"' in answer
    assert 'RETRY_AGAIN_LABEL = "再问一次"' in answer
    chain = _read(STATIC / "js" / "chain-view.js")
    for name in CHAIN_EXPORTS:
        assert f"export function {name}" in chain or f"function {name}" in chain


def test_workspace_and_static_assets_are_served():
    svc = QueryService.create_offline()
    client = TestClient(create_app(query_service=svc))
    response = client.get("/web")
    assert response.status_code == HTTP_OK
    assert "知识推理工作台" in response.text
    for path in REQUIRED_FILES:
        if path.is_relative_to(STATIC):
            asset = "/web/static/" + path.relative_to(STATIC).as_posix()
            assert client.get(asset).status_code == HTTP_OK, asset
    assert client.get("/v1/me").json()["data"]["role"] == "reader"
    svc.close()


def test_web_query_feedback_and_stream_still_work():
    svc = QueryService.create_offline()
    client = TestClient(create_app(query_service=svc))
    response = client.post("/v1/query", json={"question": "Who is the CEO of Apex Holdings?"})
    assert response.status_code == HTTP_OK
    data = response.json()["data"]
    assert data["answer"] and data["query_id"]
    feedback = client.post(
        "/v1/feedback",
        json={"query_id": data["query_id"], "accurate": True, "reason": "ui-test"},
    )
    assert feedback.status_code == HTTP_OK
    with client.stream(
        "POST", "/v1/query/stream", json={"question": "Who is the CEO of Apex Holdings?"}
    ) as response:
        assert response.status_code == HTTP_OK
        stream = "".join(response.iter_text())
    assert "event:" in stream and "answer" in stream
    svc.close()
