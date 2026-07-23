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


# Event type constants matching api-and-ui.md
EVENT_TRIAGE = "triage"
EVENT_SUB_QUESTION = "sub_question"
EVENT_HOP_DONE = "hop_done"
EVENT_THINKING = "thinking"
EVENT_ANSWER = "answer"
EVENT_ERROR = "error"
EVENT_CACHE_HIT = "cache_hit"
