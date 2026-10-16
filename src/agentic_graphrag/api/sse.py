"""SSE helpers for streaming query progress (FR-API-02 / P3-PERF-06)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any


def format_sse(event: str, data: dict[str, Any] | str, *, event_id: str | None = None) -> str:
    """Format one SSE message block."""
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    lines.append("")
    return "\n".join(lines) + "\n"


def iter_query_events(events: list[tuple[str, dict[str, Any]]]) -> Iterator[str]:
    """Yield SSE frames from (event_type, payload) pairs."""
    for i, (etype, payload) in enumerate(events):
        yield format_sse(etype, payload, event_id=str(i))


# Event type constants matching api-and-ui.md (defined in the app layer).
from agentic_graphrag.app.stream_events import (  # noqa: E402
    EVENT_ANSWER,
    EVENT_CACHE_HIT,
    EVENT_ERROR,
    EVENT_HOP_DONE,
    EVENT_SUB_QUESTION,
    EVENT_THINKING,
    EVENT_TRIAGE,
)

__all__ = [
    "EVENT_ANSWER",
    "EVENT_CACHE_HIT",
    "EVENT_ERROR",
    "EVENT_HOP_DONE",
    "EVENT_SUB_QUESTION",
    "EVENT_THINKING",
    "EVENT_TRIAGE",
    "format_sse",
    "iter_query_events",
]
