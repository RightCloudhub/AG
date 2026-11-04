"""Circuit breakers for live LLM HTTP (ARCH_OPTIMIZATION_V2 O-8).

A breaker opens on either rule:

* ``failure_threshold`` consecutive failures (V1 rule, kept as the fast trip);
* within the last ``window_seconds``, at least ``min_calls`` calls and a
  failure ratio ≥ ``failure_rate`` (``window_seconds=0`` disables this rule).

After ``cooldown_seconds`` it half-opens and admits one probe at a time; a success
closes it. ``allow`` only reports; ``acquire`` is what a real call must hold.

:class:`CircuitBreakerSet` keeps one breaker per route (``chat:strong``,
``chat:light``, ``embed``). V1 shared one breaker for all three, so a broken
embedding endpoint also blocked chat and STRONG failures blocked LIGHT, which
made any tier fallback impossible. :func:`shared_breakers` keeps one set per LLM
endpoint for the life of the process: providers are built per query, so a set owned
by a provider forgot every failure and each query paid the outage's timeouts again.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Hashable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from http import HTTPStatus

import httpx

ROUTE_STRONG = "chat:strong"
ROUTE_LIGHT = "chat:light"
ROUTE_EMBED = "embed"

# Rejected for what this request contains, not because the upstream is unhealthy.
# One tenant's oversized prompt must not stop the LLM for everyone.
_REQUEST_ERRORS = frozenset(
    {
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    }
)


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class CircuitPermit:
    """Identify the circuit generation that admitted an in-flight request."""

    generation: int


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
    _generation: int = 0
    _probe: CircuitPermit | None = None
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
        """Whether a call could go through now. Reserves nothing."""
        assert self._lock is not None
        with self._lock:
            self._maybe_half_open()
            return self._state is CircuitState.CLOSED or (
                self._state is CircuitState.HALF_OPEN and self._probe is None
            )

    def acquire(self) -> CircuitPermit | None:
        """Permission for one real call; half-open lets a single probe through."""
        assert self._lock is not None
        with self._lock:
            self._maybe_half_open()
            if self._state is CircuitState.OPEN:
                return None
            permit = CircuitPermit(self._generation)
            if self._state is CircuitState.HALF_OPEN:
                if self._probe is not None:
                    return None
                self._probe = permit
            return permit

    def release_probe(self, *, permit: CircuitPermit | None = None) -> None:
        """The call said nothing about upstream health; let another one probe."""
        assert self._lock is not None
        with self._lock:
            if permit is None or self._probe is permit:
                self._probe = None

    def record_success(self, *, permit: CircuitPermit | None = None) -> None:
        assert self._lock is not None
        with self._lock:
            if permit is not None and permit.generation != self._generation:
                return
            self._note(ok=True)
            self._failures = 0
            if self._state is not CircuitState.CLOSED:
                self._generation += 1
            self._state = CircuitState.CLOSED
            self._probe = None

    def record_failure(self, *, permit: CircuitPermit | None = None) -> None:
        assert self._lock is not None
        with self._lock:
            if permit is not None and permit.generation != self._generation:
                return
            self._note(ok=False)
            self._failures += 1
            self._probe = None
            if (
                self._state is CircuitState.HALF_OPEN
                or self._failures >= self.failure_threshold
                or self._rate_tripped()
            ):
                self._state = CircuitState.OPEN
                self._opened_at = time.time()
                self._generation += 1

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
            self,
            _failures=0,
            _opened_at=0.0,
            _state=CircuitState.CLOSED,
            _generation=0,
            _probe=None,
            _window=deque(),
        )


def note_call_failure(
    breaker: CircuitBreaker, exc: Exception, *, permit: CircuitPermit | None = None
) -> None:
    """Count a failed call against the breaker unless the request itself was at fault."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _REQUEST_ERRORS:
        breaker.release_probe(permit=permit)
    else:
        breaker.record_failure(permit=permit)


class CircuitBreakerSet:
    """One independent breaker per route, all cloned from ``template``."""

    def __init__(
        self,
        template: CircuitBreaker | None = None,
        *,
        scope: tuple[Hashable, ...] | None = None,
    ) -> None:
        self.template = template or CircuitBreaker()
        self.scope = scope
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

    def for_client(self, name: str) -> CircuitBreakerSet:
        """Separate breakers for a related client, such as a verifier on its own model."""
        if self.scope is None:
            return CircuitBreakerSet(self.template)
        return shared_breakers((*self.scope, name), self.template)


_SHARED: dict[tuple[Hashable, ...], CircuitBreakerSet] = {}
_SHARED_LOCK = threading.Lock()
_STATE_SEVERITY = {CircuitState.CLOSED: 0, CircuitState.HALF_OPEN: 1, CircuitState.OPEN: 2}


def shared_breakers(scope: tuple[Hashable, ...], template: CircuitBreaker) -> CircuitBreakerSet:
    """The process-wide breakers of one LLM endpoint; budgets stay per query."""
    key = (
        *scope,
        template.failure_threshold,
        template.cooldown_seconds,
        template.window_seconds,
        template.failure_rate,
        template.min_calls,
    )
    with _SHARED_LOCK:
        return _SHARED.setdefault(key, CircuitBreakerSet(template, scope=scope))


def shared_breaker_states() -> dict[str, str]:
    """Worst state per route across this process's LLM endpoints (health, metrics)."""
    with _SHARED_LOCK:
        sets = list(_SHARED.values())
    worst: dict[str, str] = {}
    for breakers in sets:
        for route, state in breakers.states().items():
            current = worst.get(route, CircuitState.CLOSED.value)
            if _STATE_SEVERITY[CircuitState(state)] >= _STATE_SEVERITY[CircuitState(current)]:
                worst[route] = state
    return worst


def reset_shared_breakers() -> None:
    """Forget all shared breaker state (tests)."""
    with _SHARED_LOCK:
        _SHARED.clear()
