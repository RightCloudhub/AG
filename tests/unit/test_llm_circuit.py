"""Circuit breaker unit tests."""

from __future__ import annotations

import time

from agentic_graphrag.llm.circuit import CircuitBreaker, CircuitState


def test_opens_after_threshold():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60.0)
    assert cb.allow()
    cb.record_failure()
    cb.record_failure()
    assert cb.state is CircuitState.CLOSED
    cb.record_failure()
    assert cb.state is CircuitState.OPEN
    assert not cb.allow()


def test_success_resets():
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=60.0)
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert cb.state is CircuitState.CLOSED


def test_half_open_after_cooldown():
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
    cb.record_failure()
    assert cb.state is CircuitState.OPEN
    time.sleep(0.06)
    assert cb.state is CircuitState.HALF_OPEN
    assert cb.allow()
