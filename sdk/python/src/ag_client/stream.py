"""Bounded SSE decoding; no replay or retry of an interrupted paid request."""

import json
from dataclasses import dataclass, field
from typing import Any

from ag_client.errors import APIError, ProtocolError, StreamInterrupted
from ag_client.models import QueryResult
from ag_client.transport import parse_model

KNOWN_EVENTS = frozenset(
    {
        "cache_hit",
        "triage",
        "thinking",
        "sub_question",
        "hop_done",
        "degraded",
        "global_progress",
        "summary_progress",
        "answer",
        "error",
    }
)
MAX_EVENT_CHARS = 1_048_576


@dataclass(frozen=True)
class StreamEvent:
    event: str
    data: dict[str, Any]
    id: str | None = None

    @property
    def result(self) -> QueryResult | None:
        return parse_model(QueryResult, self.data) if self.event == "answer" else None


@dataclass
class SSEDecoder:
    """Decode line-framed events. Unknown event payloads need not be JSON."""

    _event: str = "message"
    _id: str | None = None
    _data: list[str] = field(default_factory=list)
    _size: int = 0
    _first_line: bool = True

    def feed(self, line: str) -> StreamEvent | None:
        if self._first_line:
            line = line.removeprefix("\ufeff")
            self._first_line = False
        if not line:
            return self._dispatch()
        self._size += len(line)
        if self._size > MAX_EVENT_CHARS:
            raise ProtocolError("SSE event exceeds the client size limit")
        if line.startswith(":"):
            return None
        key, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if key == "event":
            self._event = value
        elif key == "data":
            self._data.append(value)
        elif key == "id" and "\x00" not in value:
            self._id = value or None
        return None

    def _dispatch(self) -> StreamEvent | None:
        event, data = self._event, "\n".join(self._data)
        self._event, self._data, self._size = "message", [], 0
        if event not in KNOWN_EVENTS or not data:
            return None
        try:
            payload = json.loads(data)
        except ValueError as exc:
            raise ProtocolError("Known SSE event contains invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ProtocolError("SSE event payload must be an object")
        return StreamEvent(event, payload, self._id)


@dataclass
class StreamState:
    query_id: str | None = None
    terminal: bool = False

    def accept(self, event: StreamEvent) -> None:
        query_id = event.data.get("query_id")
        if isinstance(query_id, str):
            self.query_id = query_id
        if event.event == "error":
            self.terminal = True
            raise APIError(
                str(event.data.get("code") or "STREAM_ERROR"),
                str(event.data.get("message") or "Query stream failed"),
                details={"query_id": self.query_id},
            )
        if event.event == "answer":
            parse_model(QueryResult, event.data)
            self.terminal = True

    def finish(self) -> None:
        if not self.terminal:
            raise StreamInterrupted(query_id=self.query_id)
