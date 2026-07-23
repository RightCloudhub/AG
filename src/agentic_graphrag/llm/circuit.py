"""Simple consecutive-failure circuit breaker for live LLM HTTP."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Open after N consecutive failures; cool down then half-open."""

    failure_threshold: int = 2
    cooldown_seconds: float = 20.0
    _failures: int = 0
    _opened_at: float = 0.0
    _state: CircuitState = CircuitState.CLOSED
    _lock: threading.Lock | None = None

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
            self._failures = 0
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        assert self._lock is not None
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.time()

    def _maybe_half_open(self) -> None:
        if self._state is not CircuitState.OPEN:
            return
        if time.time() - self._opened_at >= self.cooldown_seconds:
            self._state = CircuitState.HALF_OPEN
