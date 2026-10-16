"""Refactor §0-A / §8.1: app/ layer shared by drivers; eval runs the production path."""

from __future__ import annotations

from agentic_graphrag.config import resolve_path


def test_api_service_is_an_alias_of_app_query_service() -> None:
    import agentic_graphrag.api.service as old
    import agentic_graphrag.app.query_service as new
    from agentic_graphrag.app import QueryApp

    assert old is new and QueryApp is new.QueryService


def test_app_layer_does_not_import_api() -> None:
    import ast
    from pathlib import Path

    app_dir = Path(resolve_path("src/agentic_graphrag/app"))
    for path in app_dir.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("agentic_graphrag.api"), path.name


def test_eval_app_runs_production_path_and_returns_chain() -> None:
    from agentic_graphrag.cli.cases_run import build_eval_app, run_case_row_via_app

    app = build_eval_app(
        no_llm=True,
        use_neo4j=False,
        seed_triples=resolve_path("data/processed/seed_triples.jsonl"),
        enable_triage=True,
    )
    try:
        assert app.profile == "eval" and not app.enable_cache
        row = run_case_row_via_app(
            {"id": "c1", "question": "Who is the CEO of NovaTech Industries?"}, app=app
        )
        assert row["case_id"] == "c1" and "prediction" in row and row.get("route")
    finally:
        app.close()
