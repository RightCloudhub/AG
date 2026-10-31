"""Structural and API smoke tests for the zero-build Vue workspace (推理笔记本 shell)."""

from __future__ import annotations

import re
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
    "tokens.css",
    "base.css",
    "shell.css",
    "notebook.css",
    "inspector.css",
    "wizard.css",
    "admin.css",
    "responsive.css",
)
VIEW_FILES = (
    "views/chat.js",
    "admin/graph.js",
    "admin/knowledge.js",
    "admin/review.js",
    "admin/ops.js",
)
CORE_JS = (
    "app.js",
    "api.js",
    "contracts.js",
    "stream.js",
    "responses.js",
    "i18n.js",
    "icons.js",
    "theme.js",
)
FEATURE_JS = (
    "chat/use_composer.js",
    "chat/tpl_composer.js",
    "chat/tpl_page.js",
    "chat/tpl_thread.js",
    "chat/hoprail.js",
    "chat/pages.js",
    "store/conversations.js",
    "store/preferences.js",
    "store/shell.js",
    "shell/tpl_topbar.js",
    "shell/tpl_notebook.js",
    "shell/tpl_inspector.js",
    "shell/use_shell.js",
    "shell/use_palette.js",
    "shell/use_inspector.js",
    "knowledge/ingest_wizard.js",
)
TEMPLATE_JS = (
    "app.js",
    "views/chat.js",
    "admin/knowledge.js",
    "chat/tpl_composer.js",
    "chat/tpl_page.js",
    "chat/tpl_thread.js",
    "shell/tpl_topbar.js",
    "shell/tpl_notebook.js",
    "shell/tpl_inspector.js",
)
REQUIRED_FILES = (
    WEB / "index.html",
    *(STATIC / "css" / name for name in CSS_FILES),
    *(STATIC / "js" / name for name in CORE_JS),
    *(STATIC / "js" / name for name in FEATURE_JS),
    *(STATIC / "js" / name for name in VIEW_FILES),
    STATIC / "vendor" / "vue.esm-browser.prod.js",
)
# Icons are drawn SVG paths (icons.js); no emoji or symbol glyph stands in for a control.
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿⭐]")
# The brief's hop palette (Prompt.md §2.4), dark and light, must survive in the tokens.
HOP_DARK = ("#5AB8F0", "#F2A93B", "#4FD1C5", "#E68BC4", "#B9A6FF", "#E8D35A")
HOP_LIGHT = ("#1A6BAA", "#9A5508", "#0F7A70", "#B23E86", "#6A4FD0", "#776300")
# The icon set is the vendored Lucide subset (Prompt.md §3.3); `mark` is the product's own glyph.
LUCIDE_VERSION = "1.65.0"
ICON_USES = re.compile(r'<ag-icon[^>]*\sname="([A-Za-z0-9]+)"|icon:\s*\'([A-Za-z0-9]+)\'')
ICON_READERS = (
    "shell/use_shell.js",
    "shell/use_palette.js",
    "chat/use_composer.js",
)
CHAT_HANDLERS = (
    "cancelStream",
    "submitFeedback",
    "selectHop",
    "openEvidence",
    "rerunFromHop",
    "saveEdit",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _js(rel: str) -> str:
    return _read(STATIC / "js" / rel)


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
    assert "推理笔记本" in html and "智能问答控制台" in html
    assert "https://" not in html, "offline shell must not request external assets"


def test_tokens_carry_both_themes_and_the_hop_palette():
    tokens = _read(STATIC / "css" / "tokens.css")
    assert '[data-theme="dark"]' in tokens and '[data-theme="light"]' in tokens
    assert "prefers-color-scheme: light" in tokens
    for colour in (*HOP_DARK, *HOP_LIGHT):
        assert colour in tokens, colour
    assert "--font-serif" in tokens and "--font-sans" in tokens and "--font-mono" in tokens
    theme = _js("theme.js")
    assert "StorageKey.THEME" in theme and "prefers-color-scheme" in theme


def test_role_aware_views_and_api_contracts():
    app = _js("app.js")
    for view in VIEW_FILES:
        assert f"[View.{Path(view).stem.upper()}]" in app
    assert "capabilities.value" in app and "handleHashChange" in app
    shell = _js("shell/use_shell.js")
    assert "fetchMe" in shell and "NAV_ITEMS.filter" in shell
    contracts = _js("contracts.js")
    api = _js("api.js")
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
    chat = _js("views/chat.js")
    for event in StreamEvent:
        if event is not StreamEvent.FINAL_CHAIN:
            assert f"StreamEvent.{event.name}" in chat and repr(event.value) in contracts


def test_vue_runtime_remains_pinned_and_local():
    app = _js("app.js")
    assert "../vendor/vue.esm-browser.prod.js" in app and "createApp" in app
    assert f"vue v{VUE_VERSION}" in _read(STATIC / "vendor" / "vue.esm-browser.prod.js")[:150]
    assert not list(WEB.rglob("package.json")), "web must remain zero-build"


def test_frontend_injection_and_external_asset_safety():
    text = "\n".join(_read(path) for path in _owned_sources())
    assert "v-html" not in text and ".innerHTML" not in text
    css = "\n".join(_read(path) for path in (STATIC / "css").glob("*.css"))
    assert "http://" not in css and "https://" not in css
    assert "prefers-reduced-motion" in css and ":focus-visible" in css
    assert "backdrop-filter" not in css and "gradient(" not in css, "flat surfaces only"
    for rel in TEMPLATE_JS:
        assert 'style="' not in _js(rel), rel
    assert not EMOJI.search(text), "icons are drawn SVG paths (icons.js), never emoji"


def test_shell_is_topbar_notebook_page_and_inspector():
    topbar = _js("shell/tpl_topbar.js")
    assert 'class="topbar"' in topbar and 'name="mark"' in topbar and "openPalette" in topbar
    assert 'class="health"' in topbar and 'role="status"' in topbar and "toggleTheme" in topbar
    assert 'type="password"' in topbar and "settings-title" in topbar
    assert 'role="dialog"' in topbar
    notebook = _js("shell/tpl_notebook.js")
    assert 'class="notebook"' in notebook and 'class="nb-item' in notebook
    assert "adminNav" in notebook
    inspector = _js("shell/tpl_inspector.js")
    assert 'class="inspector"' in inspector and 'role="tablist"' in inspector
    for tab in ("evidence", "subgraph", "log"):
        assert f"insp-panel-{tab}" in inspector, tab
    assert 'class="evcard"' in inspector and "inspResizeStart" in inspector
    app = _js("app.js")
    assert "app.component('ag-icon', AgIcon)" in app and "Shortcut.PALETTE" in app
    for view in VIEW_FILES[1:]:
        source = _js(view)
        assert 'class="btn btn--icon btn--ghost menu-btn"' in source, view
        assert "openNotebook" in source, view
        assert '<h1 class="view-header__title">' in source, view


def test_question_page_has_title_rail_conclusion_meta():
    page = _js("chat/tpl_page.js")
    assert "pi === 0 ? 'h1' : 'h2'" in page and 'class="qpage__title"' in page
    assert 'class="hoprail"' in page and 'role="list"' in page
    assert 'class="hop__node"' in page
    assert 'class="cite"' in page and 'class="popover"' in page and 'class="qmeta"' in page
    assert 'class="answer__skel"' in page and "QueryStatus.NO_ANSWER" in page
    assert "T.answer.supply" in page
    assert 'class="welcome__grid"' in page and 'class="hint-card__dot"' in page
    assert 'aria-live="polite"' in page and "aria-busy" in page
    composer = _js("chat/tpl_composer.js")
    assert 'class="composer__box"' in composer and "composer__send--stop" in composer
    assert 'role="listbox"' in composer and "slash.open" in composer
    assert "mention.open" in composer
    chat = _js("views/chat.js")
    for handler in CHAT_HANDLERS:
        assert f"function {handler}" in chat, handler
    assert "abortCtrl.value.abort()" in chat and "postFeedback" in chat
    assert "railFromProgress" in chat and "railFromAnswer" in chat and "inspShow" in chat


def test_workspace_and_static_assets_are_served():
    svc = QueryService.create_offline()
    try:
        with TestClient(create_app(query_service=svc)) as client:
            response = client.get("/web")
            assert response.status_code == HTTP_OK and "推理笔记本" in response.text
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


def test_icons_are_the_vendored_lucide_subset_and_every_name_resolves():
    vendor = _read(STATIC / "vendor" / "lucide-icons.js")
    assert f"lucide v{LUCIDE_VERSION}" in vendor and "ISC" in vendor
    declared = dict(re.findall(r"^  ([A-Za-z]+): '([a-z0-9-]+)',$", vendor, re.M))
    geometry = set(re.findall(r"^  '([a-z0-9-]+)': \[", vendor, re.M))
    assert declared, "the vendored file carries the name -> Lucide id table"
    assert set(declared.values()) == geometry, set(declared.values()) ^ geometry
    icons = _js("icons.js")
    assert "from '../vendor/lucide-icons.js'" in icons and "mark:" in icons
    assert "ICON_IDS" in icons and "lucide-icons.js" in icons
    used = set()
    for rel in (*TEMPLATE_JS, *ICON_READERS):
        for literal, key in ICON_USES.findall(_js(rel)):
            used.add(literal or key)
    assert len(used) > 10, f"the icon scan found too few names: {sorted(used)}"
    missing = sorted(name for name in used if name != "mark" and name not in declared)
    assert not missing, f"templates ask for icons the vendored set does not carry: {missing}"


def test_motion_follows_the_demo_vocabulary_and_reduced_motion_drops_travel():
    base = _read(STATIC / "css" / "base.css")
    for keyframe in ("enter-note", "enter-node", "panel-in", "breathe", "spin", "fade-in"):
        assert f"@keyframes {keyframe} " in base, keyframe
    assert "--t-stagger: 55ms" in _read(STATIC / "css" / "tokens.css")
    notebook = _read(STATIC / "css" / "notebook.css")
    assert ".hoprail__track .hop--new:nth-child(2)" in notebook
    assert "enter-node" in notebook and "enter-note" in notebook
    responsive = _read(STATIC / "css" / "responsive.css")
    assert "animation-delay: 0ms !important" in responsive
    assert ".inspector { animation: none; }" in responsive, (
        "the drawer must keep its transform transition"
    )
    assert "@media (prefers-reduced-motion: reduce)" in responsive
