"""Full-chain tracing with query_id correlation (NFR-08 / P3-OP-03)."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SpanEvent:
    name: str
    query_id: str
    started_at: float
    ended_at: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    parent: str | None = None
    span_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "name": self.name,
            "query_id": self.query_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": (
                int((self.ended_at - self.started_at) * 1000) if self.ended_at else None
            ),
            "attributes": dict(self.attributes),
            "parent": self.parent,
        }


@dataclass
class TraceContext:
    query_id: str
    tenant_id: str = ""
    user_id: str = ""
    spans: list[SpanEvent] = field(default_factory=list)

    def add_event(self, name: str, **attributes: Any) -> SpanEvent:
        span = SpanEvent(
            name=name,
            query_id=self.query_id,
            started_at=time.time(),
            ended_at=time.time(),
            attributes=attributes,
        )
        self.spans.append(span)
        return span

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "spans": [s.to_dict() for s in self.spans],
        }


class Tracer:
    def __init__(self, *, max_traces: int = 2000) -> None:
        self.max_traces = max_traces
        self._traces: dict[str, TraceContext] = {}
        self._lock = threading.Lock()

    def start(self, query_id: str | None = None, **kwargs: Any) -> TraceContext:
        qid = query_id or str(uuid.uuid4())
        ctx = TraceContext(query_id=qid, **kwargs)
        with self._lock:
            self._traces[qid] = ctx
            if len(self._traces) > self.max_traces:
                # Drop oldest insertion order
                oldest = next(iter(self._traces))
                del self._traces[oldest]
        return ctx

    def get(self, query_id: str) -> TraceContext | None:
        with self._lock:
            return self._traces.get(query_id)

    def list_ids(self, limit: int = 100) -> list[str]:
        with self._lock:
            return list(self._traces.keys())[-limit:]


_TRACER = Tracer()


def get_tracer() -> Tracer:
    return _TRACER


@contextmanager
def span(
    ctx: TraceContext,
    name: str,
    **attributes: Any,
) -> Iterator[SpanEvent]:
    event = SpanEvent(
        name=name,
        query_id=ctx.query_id,
        started_at=time.time(),
        attributes=dict(attributes),
    )
    try:
        yield event
    finally:
        event.ended_at = time.time()
        ctx.spans.append(event)
