"""Query stream event names (contract shared by SSE, CLI and eval drivers)."""

from agentic_graphrag.agent.loop_stream_events import (
    EVENT_HOP_DONE,
    EVENT_SUB_QUESTION,
    EVENT_THINKING,
    EVENT_TRIAGE,
)
from agentic_graphrag.core.enums import StreamEvent

EVENT_ANSWER = StreamEvent.ANSWER
EVENT_ERROR = StreamEvent.ERROR
EVENT_CACHE_HIT = StreamEvent.CACHE_HIT

__all__ = [
    "EVENT_ANSWER",
    "EVENT_CACHE_HIT",
    "EVENT_ERROR",
    "EVENT_HOP_DONE",
    "EVENT_SUB_QUESTION",
    "EVENT_THINKING",
    "EVENT_TRIAGE",
]
