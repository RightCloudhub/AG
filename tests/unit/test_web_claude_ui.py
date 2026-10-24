"""Structural and API smoke tests for the current zero-build Vue workspace."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.config import ROOT_DIR
from agentic_graphrag.core.enums import StreamEvent

HTTP_OK = 200
VUE_VERSION = "3.5.13"
MAX_FILE_LINES = 300
WEB = ROOT_DIR / "web"
STATIC = WEB / "static"
CSS_FILES = (
    "design-system.css",
    "layout.css",
    "controls.css",
    "components.css",
    "chat.css",
    "answers.css",
    "views.css",
    "responsive.css",
)
VIEW_FILES = ("chat.js", "knowledge.js", "review.js", "graph.js", "ops.js")
REQUIRED_FILES = (
    WEB / "index.html",
    *(STATIC / "css" / name for name in CSS_FILES),
    *(STATIC / "js" / name for name in ("app.js", "api.js", "contracts.js", "stream.js")),
    *(STATIC / "js" / "views" / name for name in VIEW_FILES),
    STATIC / "vendor" / "vue.esm-browser.prod.js",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _owned_sources() -> list[Path]:
    return [
        path
        for path in WEB.rglob("*")
        if path.suffix in {".html", ".js", ".css"} and STATIC / "vendor" not in path.parents
    ]


def test_required_workspace_files_and_size_budgets():
    missing = [str(path.relative_to(ROOT_DIR)) for path in REQUIRED_FILES if not path.is_file()]
    assert not missing, f"missing workspace files: {missing}"
    oversized = [
        f"{path.relative_to(ROOT_DIR)}: {len(_read(path).splitlines())}"
        for path in _owned_sources()
        if len(_read(path).splitlines()) > MAX_FILE_LINES
    ]
    assert not oversized, f"frontend size budget exceeded: {oversized}"


def test_html_shell_and_all_local_stylesheets():
    html = _read(WEB / "index.html")
    assert 'lang="zh-CN"' in html and 'id="app"' in html
    assert 'type="module"' in html and "/web/static/js/app.js" in html
    for name in CSS_FILES:
        assert f"/web/static/css/{name}" in html
    assert "智能问答控制台" in html
    assert "https://" not in html, "offline shell must not request external assets"


def test_role_aware_views_and_api_contracts():
    app = _read(STATIC / "js" / "app.js")
    for view in VIEW_FILES:
        assert f"[View.{Path(view).stem.upper()}]" in app
    assert "capabilities.value" in app and "NAV_ITEMS.filter" in app
    assert "fetchMe" in app and "handleHashChange" in app
    contracts = _read(STATIC / "js" / "contracts.js")
    api = _read(STATIC / "js" / "api.js")
    for endpoint in (
        "ME",
        "QUERY",
        "STREAM",
        "FEEDBACK",
        "DOCS",
        "INGEST_TASKS",
        "REVIEW_QUEUE",
        "ENTITIES",
        "METRICS",
        "BUDGET",
        "AUDIT_EVENTS",
    ):
        assert f"ApiEndpoint.{endpoint}" in api
    chat = _read(STATIC / "js" / "views" / "chat.js")
    for event in StreamEvent:
        if event is not StreamEvent.FINAL_CHAIN:
            assert f"StreamEvent.{event.name}" in chat and repr(event.value) in contracts


def test_vue_runtime_remains_pinned_and_local():
    app = _read(STATIC / "js" / "app.js")
    assert "../vendor/vue.esm-browser.prod.js" in app and "createApp" in app
    assert f"vue v{VUE_VERSION}" in _read(STATIC / "vendor" / "vue.esm-browser.prod.js")[:150]
    assert not list(WEB.rglob("package.json")), "web must remain zero-build"


def test_frontend_injection_and_external_asset_safety():
    text = "\n".join(_read(path) for path in _owned_sources())
    assert "v-html" not in text and ".innerHTML" not in text
    css = "\n".join(_read(path) for path in (STATIC / "css").glob("*.css"))
    assert "http://" not in css and "https://" not in css
    assert "prefers-reduced-motion" in css and ":focus-visible" in css


def test_chat_and_answer_interactions_remain_available():
    chat = _read(STATIC / "js" / "views" / "chat.js")
    for handler in ("cancelStream", "submitFeedback", "toggleSteps"):
        assert f"function {handler}" in chat and f'@click="{handler}' in chat
    assert "abortCtrl.value.abort()" in chat and "postFeedback" in chat
    assert "msg.data.steps" in chat and "msg.data.answer" in chat
    assert "msg.data.evidence" in chat and "ev.content" in chat


def test_workspace_and_static_assets_are_served():
    svc = QueryService.create_offline()
    try:
        with TestClient(create_app(query_service=svc)) as client:
            response = client.get("/web")
            assert response.status_code == HTTP_OK and "智能问答控制台" in response.text
            for path in REQUIRED_FILES:
                if path.is_relative_to(STATIC):
                    asset = "/web/static/" + path.relative_to(STATIC).as_posix()
                    assert client.get(asset).status_code == HTTP_OK, asset
            assert client.get("/v1/me").json()["data"]["role"] == "reader"
    finally:
        svc.close()


def test_web_query_feedback_and_stream_still_work():
    svc = QueryService.create_offline()
    try:
        with TestClient(create_app(query_service=svc)) as client:
            response = client.post(
                "/v1/query", json={"question": "Who is the CEO of Apex Holdings?"}
            )
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
            assert f"event: {StreamEvent.ANSWER}" in stream
    finally:
        svc.close()
