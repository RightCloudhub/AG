"""Query stream event names (contract shared by SSE, CLI and eval drivers)."""

from agentic_graphrag.agent.loop_stream_events import (
    EVENT_HOP_DONE,
    EVENT_SUB_QUESTION,
    EVENT_THINKING,
    EVENT_TRIAGE,
)

EVENT_ANSWER = "answer"
EVENT_ERROR = "error"
EVENT_CACHE_HIT = "cache_hit"

__all__ = [
    "EVENT_ANSWER",
    "EVENT_CACHE_HIT",
    "EVENT_ERROR",
    "EVENT_HOP_DONE",
    "EVENT_SUB_QUESTION",
    "EVENT_THINKING",
    "EVENT_TRIAGE",
]
