"""Domain events and the EventBus port (refactor §0-D).

The write side (ingest, extraction, review) publishes facts about what changed;
the read side (caches, entity lexicon, index version) subscribes. In-process the
bus is synchronous; a live deployment swaps in an outbox + Redis Streams
adapter behind the same :class:`EventBus` protocol.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter, defaultdict
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
FailureHook = Callable[[DomainEvent, BaseException], None]


@dataclass(frozen=True)
class Subscription:
    """A handler plus its delivery policy (ARCH_OPTIMIZATION_V2 O-4).

    ``critical`` handlers are retried ``retries`` times; if they still fail,
    ``on_failure`` runs (the fail-safe, e.g. drop every cache) and the failure
    is counted. Non-critical handlers keep the log-and-continue behaviour.
    """

    handler: Handler
    critical: bool = False
    retries: int = 0
    on_failure: FailureHook | None = None

    @property
    def name(self) -> str:
        return getattr(self.handler, "__qualname__", repr(self.handler))


@runtime_checkable
class EventBus(Protocol):
    def publish(self, event: DomainEvent) -> None: ...

    def subscribe(
        self,
        event_type: type[DomainEvent],
        handler: Handler,
        *,
        critical: bool = False,
        retries: int = 0,
        on_failure: FailureHook | None = None,
    ) -> None: ...


class InProcessEventBus:
    """Synchronous in-process bus. Handler errors are logged, never propagated.

    Subscribing to :class:`DomainEvent` receives every event.
    """

    def __init__(self) -> None:
        self._subs: dict[type[DomainEvent], list[Subscription]] = defaultdict(list)
        self._lock = threading.RLock()
        #: ``"<event>:<handler>"`` → final failures (after retries).
        self.handler_failures: Counter[str] = Counter()

    def subscribe(
        self,
        event_type: type[DomainEvent],
        handler: Handler,
        *,
        critical: bool = False,
        retries: int = 0,
        on_failure: FailureHook | None = None,
    ) -> None:
        sub = Subscription(handler, critical, max(0, int(retries)), on_failure)
        with self._lock:
            self._subs[event_type].append(sub)

    def publish(self, event: DomainEvent) -> None:
        with self._lock:
            subs = [s for etype, ss in self._subs.items() if isinstance(event, etype) for s in ss]
        for sub in subs:
            self._deliver(sub, event)

    def _deliver(self, sub: Subscription, event: DomainEvent) -> None:
        attempts = 1 + (sub.retries if sub.critical else 0)
        error: BaseException | None = None
        for _ in range(attempts):
            try:
                sub.handler(event)
                return
            except Exception as exc:  # noqa: BLE001 — one bad subscriber must not break writers
                error = exc
        level = logging.ERROR if sub.critical else logging.WARNING
        logger.log(level, "event handler %s failed for %s", sub.name, event.name, exc_info=error)
        self.handler_failures[f"{event.name}:{sub.name}"] += 1
        if sub.critical and sub.on_failure is not None and error is not None:
            self._fail_safe(sub, event, error)

    @staticmethod
    def _fail_safe(sub: Subscription, event: DomainEvent, error: BaseException) -> None:
        try:
            sub.on_failure(event, error)  # type: ignore[misc]
        except Exception:  # noqa: BLE001 — the fail-safe itself must never raise
            logger.exception("fail-safe for %s failed on %s", sub.name, event.name)


class RecordingEventBus(InProcessEventBus):
    """In-process bus that also keeps every published event (tests / eval)."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[DomainEvent] = []

    def publish(self, event: DomainEvent) -> None:
        self.events.append(event)
        super().publish(event)
