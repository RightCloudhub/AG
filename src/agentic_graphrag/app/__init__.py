"""Application layer (refactor §0-A).

One set of use cases for every driver — ``api/`` (HTTP/SSE), ``cli/``,
``eval/`` and workers — so caching, budgets and audit behave identically
everywhere and evaluation measures the production path.

* :class:`QueryApp` — run / stream a question (cache → budget → pipeline →
  audit → metrics). Alias of the historic ``QueryService``.
* :class:`IngestApp` — upload/ingest write path.
* :class:`ReviewApp` — record and apply review decisions.

Exports are resolved lazily to keep ``import agentic_graphrag.app.<module>``
free of import cycles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentic_graphrag.app.ingest_app import IngestApp as IngestApp
    from agentic_graphrag.app.query_service import QueryService as QueryApp
    from agentic_graphrag.app.query_service import QueryService as QueryService
    from agentic_graphrag.app.query_service import build_default_service as build_query_app
    from agentic_graphrag.app.review_app import ReviewApp as ReviewApp

__all__ = ["IngestApp", "QueryApp", "QueryService", "ReviewApp", "build_query_app"]


def __getattr__(name: str) -> object:
    if name in {"QueryApp", "QueryService"}:
        from agentic_graphrag.app.query_service import QueryService

        return QueryService
    if name == "build_query_app":
        from agentic_graphrag.app.query_service import build_default_service

        return build_default_service
    if name == "IngestApp":
        from agentic_graphrag.app.ingest_app import IngestApp

        return IngestApp
    if name == "ReviewApp":
        from agentic_graphrag.app.review_app import ReviewApp

        return ReviewApp
    raise AttributeError(name)
