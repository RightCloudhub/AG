"""Domain events and the EventBus port (refactor §0-D).

The write side (ingest, extraction, review) publishes facts about what changed;
the read side (caches, entity lexicon, index version) subscribes. In-process the
bus is synchronous; a live deployment swaps in an outbox + Redis Streams
adapter behind the same :class:`EventBus` protocol.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class DomainEvent:
    """Base class: every event has an id, a timestamp and an optional tenant."""

    tenant_id: str = ""
    event_id: str = field(default_factory=lambda: uuid4().hex)
    occurred_at: str = field(default_factory=_now)

    @property
    def name(self) -> str:
        return type(self).__name__


@dataclass(frozen=True)
class GraphCommitted(DomainEvent):
    """Facts were written to the graph (ingest, incremental update, review)."""

    source: str = ""  # "ingest" | "review" | "incremental" | ...
    doc_id: str = ""
    relations_written: int = 0
    entities_written: int = 0


@dataclass(frozen=True)
class IndexUpdated(DomainEvent):
    """The read-side index version advanced; caches keyed on it are now stale."""

    version: int = 0


@dataclass(frozen=True)
class ReviewDecided(DomainEvent):
    """A human review item was decided."""

    review_id: str = ""
    review_type: str = ""
    decision: str = ""
    applied: bool = False


Handler = Callable[[Any], None]


@runtime_checkable
class EventBus(Protocol):
    def publish(self, event: DomainEvent) -> None: ...

    def subscribe(self, event_type: type[DomainEvent], handler: Handler) -> None: ...


class InProcessEventBus:
    """Synchronous in-process bus. Handler errors are logged, never propagated.

    Subscribing to :class:`DomainEvent` receives every event.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[Handler]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, event_type: type[DomainEvent], handler: Handler) -> None:
        with self._lock:
            self._handlers[event_type].append(handler)

    def publish(self, event: DomainEvent) -> None:
        with self._lock:
            handlers = [
                h for etype, hs in self._handlers.items() if isinstance(event, etype) for h in hs
            ]
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # noqa: BLE001 — one bad subscriber must not break writers
                logger.exception("event handler failed for %s", event.name)


class RecordingEventBus(InProcessEventBus):
    """In-process bus that also keeps every published event (tests / eval)."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[DomainEvent] = []

    def publish(self, event: DomainEvent) -> None:
        self.events.append(event)
        super().publish(event)
