"""Console frontend structural tests (P5-UI-02 M6 / U-12, spec §8).

Covers: §5 file inventory with line budgets, injection safety across web/,
request shapes of the console API client, the registry role→view mapping
(spec §4 table), static asset mounts, and CSS external-link policy.
Chat-behavior regression stays in test_web_claude_ui.py.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.config import ROOT_DIR

HTTP_OK = 200
VUE_PIN = "3.5.13"
WEB = ROOT_DIR / "web"
STATIC = WEB / "static"

# spec §5 module table — path (relative to web/) → line budget (hard cap 300).
CONSOLE_FILES: tuple[tuple[str, int], ...] = (
    ("index.html", 220),
    ("static/app.js", 65),
    ("static/js/api.js", 170),
    ("static/js/api-console.js", 160),
    ("static/js/router.js", 60),
    ("static/js/views/registry.js", 40),
    ("static/js/root.js", 200),
    ("static/js/views/chat.js", 240),
    ("static/js/views/knowledge.js", 250),
    ("static/js/views/review.js", 220),
    ("static/js/views/ops.js", 260),
    ("static/js/views/graph.js", 150),
    ("static/js/components/console-widgets.js", 220),
    ("static/console.css", 300),
)

# spec §4 — view id → (hash, min nav role, component name).
ROLE_VIEW_MAP = {
    "chat": ("#/chat", "anonymous", "chat-view"),
    "knowledge": ("#/knowledge", "operator", "knowledge-view"),
    "review": ("#/review", "operator", "review-view"),
    "ops": ("#/ops", "admin", "ops-view"),
    "graph": ("#/graph", "reader", "graph-view"),
}

STATIC_ROUTES = (
    "/web/static/console.css",
    "/web/static/js/router.js",
    "/web/static/js/api-console.js",
    "/web/static/js/components/console-widgets.js",
    "/web/static/js/views/registry.js",
    "/web/static/js/views/chat.js",
    "/web/static/js/views/knowledge.js",
    "/web/static/js/views/review.js",
    "/web/static/js/views/ops.js",
    "/web/static/js/views/graph.js",
)


def _read(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


def _console_text() -> str:
    """Project-authored sources only (vendored Vue runtime excluded)."""
    vendor = STATIC / "vendor"
    chunks = []
    for path in WEB.rglob("*"):
        if not path.is_file() or path.suffix not in {".html", ".js", ".css", ".md"}:
            continue
        if vendor in path.parents and path.name != "README.md":
            continue
        chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def test_console_file_inventory_and_line_budgets():
    missing = [rel for rel, _cap in CONSOLE_FILES if not (WEB / rel).is_file()]
    assert not missing, f"missing console files: {missing}"
    over = {
        rel: lines
        for rel, cap in CONSOLE_FILES
        if (lines := len((WEB / rel).read_text(encoding="utf-8").splitlines())) > cap
    }
    assert not over, f"files over line budget: {over}"


def test_vue_pin_unchanged():
    assert f'VUE_VERSION = "{VUE_PIN}"' in _read("static/app.js")


def test_injection_safety_console_scope():
    text = _console_text()
    assert "v-html" not in text
    assert ".innerHTML" not in text


def test_registry_role_view_mapping_matches_spec():
    registry = _read("static/js/views/registry.js")
    for view_id, (hash_path, min_role, component) in ROLE_VIEW_MAP.items():
        block = re.search(rf"\{{[^}}]*id: \"{view_id}\"[^}}]*\}}", registry)
        assert block, f"view {view_id} missing from registry"
        entry = block.group(0)
        assert f'minRole: "{min_role}"' in entry, view_id
        assert f'component: "{component}"' in entry, view_id
        assert hash_path == f"#/{view_id}"


def test_console_api_client_request_shapes():
    client = _read("static/js/api-console.js")
    for url in (
        "/v1/docs",
        "/v1/ingest-tasks",
        "/v1/review-queue",
        "/v1/metrics",
        "/v1/budget/snapshot",
        "/v1/audit-events",
        "/v1/graph/entities",
        "/v1/audit/queries/",
    ):
        assert url in client, url
    # decision endpoint + ReviewDecisionBody fields (decision/reviewer/note)
    assert "/decision" in client
    # multipart upload builds FormData, no JSON content-type override
    assert "FormData" in client
    assert 'form.append("files"' in client


def test_decision_body_carries_required_fields():
    review = _read("static/js/views/review.js")
    assert "postReviewDecision(" in review
    assert "decision," in review
    assert "note:" in review
    # explicit duplicate/cross-tenant decision outcomes (U-08)
    assert "409" in review
    assert "404" in review


def test_ops_audit_event_filter_params():
    ops = _read("static/js/views/ops.js")
    for param in ("since", "until", "tenant_id", "action", "limit"):
        assert param in ops, param


def test_shell_probes_me_instead_of_403():
    root = _read("static/js/root.js")
    assert "fetchMe" in root
    assert "403" not in root
    api = _read("static/js/api.js")
    assert "/v1/me" in api


def test_me_response_fields_in_backend_route():
    console_route = (
        ROOT_DIR / "src" / "agentic_graphrag" / "api" / "routes" / "console.py"
    ).read_text(encoding="utf-8")
    for field in ("tenant_id", "user_id", "role"):
        assert field in console_route, field


def test_static_console_assets_served():
    svc = QueryService.create_offline()
    app = create_app(query_service=svc)
    client = TestClient(app)
    try:
        for path in STATIC_ROUTES:
            resp = client.get(path)
            assert resp.status_code == HTTP_OK, path
    finally:
        svc.close()


def test_css_has_no_external_links():
    for css in WEB.rglob("*.css"):
        text = css.read_text(encoding="utf-8")
        assert "http://" not in text and "https://" not in text, css.name
        if "@font-face" in text:
            assert "vendor/fonts/" in text, css.name
