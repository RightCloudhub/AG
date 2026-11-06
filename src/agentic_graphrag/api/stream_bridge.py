"""Bounded SSE bridge with cooperative cancellation and stable event ordering."""

from __future__ import annotations

import asyncio
import contextvars
import queue
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any, Literal
from uuid import uuid4

from agentic_graphrag.api.sse import format_sse
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.governance.execution import ExecutionControl, current_execution
from agentic_graphrag.observability.degradation import record_degradation
from agentic_graphrag.observability.logging_setup import query_id_var

_POLL_SECONDS = 0.1
_HEARTBEAT_SECONDS = 15.0
_TERMINALS = {"answer", "error"}
Event = tuple[str, dict[str, Any]]


class StreamBridge:
    def __init__(self, source: Callable[[], Iterator[Event]]) -> None:
        self.source = source
        self.query_id = str(uuid4())
        self.control = ExecutionControl(query_id=self.query_id)
        self.queue: queue.Queue[Event | None] = queue.Queue(maxsize=1)
        self.cancelled = threading.Event()
        self.consumed = threading.Event()
        context = contextvars.copy_context()
        self.thread = threading.Thread(target=context.run, args=(self._produce,), daemon=True)

    def _put(self, item: Event | None) -> bool:
        while not self.cancelled.is_set():
            try:
                self.queue.put(item, timeout=_POLL_SECONDS)
                return True
            except queue.Full:
                continue
        return False

    def _forward(self, events: Iterator[Event]) -> bool:
        while not self.cancelled.is_set():
            item = next(events, None)
            if item is None:
                break
            self.consumed.clear()
            if not self._put(item):
                break
            if item[0] in _TERMINALS:
                return True
            # A disconnected consumer must not start another model call.
            while not self.cancelled.is_set() and not self.consumed.wait(_POLL_SECONDS):
                pass
        return False

    def _produce(self) -> None:
        events: Iterator[Event] | None = None
        token = query_id_var.set(self.query_id)
        control_token = current_execution.set(self.control)
        try:
            events = self.source()
            if not self._forward(events):
                self._put(
                    (
                        "error",
                        {"code": "STREAM_INCOMPLETE", "message": "Query ended without a result"},
                    )
                )
        except ApiError as exc:
            self._put(("error", {"code": exc.code, "message": exc.message, "details": exc.details}))
        except Exception as exc:
            record_degradation("query_stream", "stream_failed", exc)
            self._put(("error", {"code": "INTERNAL_ERROR", "message": "Query failed"}))
        finally:
            self._close(events)
            query_id_var.reset(token)
            current_execution.reset(control_token)
            self._put(None)

    def _close(self, events: Iterator[Event] | None) -> None:
        close = getattr(events, "close", None)
        if close is not None:
            try:
                close()
            except Exception as exc:
                record_degradation("query_stream", "stream_close_failed", exc)

    def _read(self) -> Event | None | Literal[False]:
        try:
            return self.queue.get(timeout=_POLL_SECONDS)
        except queue.Empty:
            return False

    async def frames(self) -> AsyncIterator[str]:
        self.thread.start()
        sequence, heartbeat = 0, time.monotonic()
        try:
            while True:
                item = await asyncio.to_thread(self._read)
                if item is None:
                    return
                if item is False and time.monotonic() - heartbeat >= _HEARTBEAT_SECONDS:
                    heartbeat = time.monotonic()
                    yield ": keep-alive\n\n"
                if item is False:
                    continue
                event, payload = item
                sequence += 1
                payload = {"query_id": self.query_id, **payload}
                yield format_sse(event, payload, event_id=f"{payload['query_id']}:{sequence}")
                self.consumed.set()
                if event in _TERMINALS:
                    return
        finally:
            self.cancelled.set()
            self.control.cancelled.set()
