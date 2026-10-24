"""Circuit breakers for live LLM HTTP (ARCH_OPTIMIZATION_V2 O-8).

A breaker opens on either rule:

* ``failure_threshold`` consecutive failures (V1 rule, kept as the fast trip);
* within the last ``window_seconds``, at least ``min_calls`` calls and a
  failure ratio ≥ ``failure_rate`` (``window_seconds=0`` disables this rule).

After ``cooldown_seconds`` it half-opens; the next success closes it.

:class:`CircuitBreakerSet` keeps one breaker per route (``chat:strong``,
``chat:light``, ``embed``). V1 shared one breaker for all three, so a broken
embedding endpoint also blocked chat and STRONG failures blocked LIGHT, which
made any tier fallback impossible.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from enum import StrEnum

ROUTE_STRONG = "chat:strong"
ROUTE_LIGHT = "chat:light"
ROUTE_EMBED = "embed"


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Consecutive-failure + sliding-window failure-rate breaker."""

    failure_threshold: int = 2
    cooldown_seconds: float = 20.0
    window_seconds: float = 0.0
    failure_rate: float = 0.5
    min_calls: int = 5
    _failures: int = 0
    _opened_at: float = 0.0
    _state: CircuitState = CircuitState.CLOSED
    _lock: threading.Lock | None = None
    _window: deque[tuple[float, bool]] = field(default_factory=deque)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        assert self._lock is not None
        with self._lock:
            self._maybe_half_open()
            return self._state

    def allow(self) -> bool:
        assert self._lock is not None
        with self._lock:
            self._maybe_half_open()
            return self._state is not CircuitState.OPEN

    def record_success(self) -> None:
        assert self._lock is not None
        with self._lock:
            self._note(ok=True)
            self._failures = 0
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        assert self._lock is not None
        with self._lock:
            self._note(ok=False)
            self._failures += 1
            if self._failures >= self.failure_threshold or self._rate_tripped():
                self._state = CircuitState.OPEN
                self._opened_at = time.time()

    def _note(self, *, ok: bool) -> None:
        if self.window_seconds <= 0:
            return
        now = time.time()
        self._window.append((now, ok))
        while self._window and now - self._window[0][0] > self.window_seconds:
            self._window.popleft()

    def _rate_tripped(self) -> bool:
        if self.window_seconds <= 0 or len(self._window) < max(1, self.min_calls):
            return False
        failed = sum(1 for _ts, ok in self._window if not ok)
        return failed / len(self._window) >= self.failure_rate

    def _maybe_half_open(self) -> None:
        if self._state is not CircuitState.OPEN:
            return
        if time.time() - self._opened_at >= self.cooldown_seconds:
            self._state = CircuitState.HALF_OPEN

    def fresh(self) -> CircuitBreaker:
        """A new breaker with this one's settings and clean state."""
        return replace(
            self, _failures=0, _opened_at=0.0, _state=CircuitState.CLOSED, _window=deque()
        )


class CircuitBreakerSet:
    """One independent breaker per route, all cloned from ``template``."""

    def __init__(self, template: CircuitBreaker | None = None) -> None:
        self.template = template or CircuitBreaker()
        self._routes: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def route(self, name: str) -> CircuitBreaker:
        with self._lock:
            if name not in self._routes:
                self._routes[name] = self.template.fresh()
            return self._routes[name]

    def states(self) -> dict[str, str]:
        with self._lock:
            routes = dict(self._routes)
        return {name: cb.state.value for name, cb in sorted(routes.items())}
