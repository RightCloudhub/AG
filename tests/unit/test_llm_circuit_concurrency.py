"""Recovery probes and older in-flight calls cannot corrupt shared breaker state."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest

from agentic_graphrag.core.ports.llm import Message
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.llm.circuit import ROUTE_STRONG, CircuitBreaker, CircuitState
from agentic_graphrag.llm.provider import LLMProvider


def _reply() -> dict[str, Any]:
    return {"choices": [{"message": {"content": "ok"}}]}


def test_slow_probe_keeps_exclusive_access_past_the_cooldown(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("agentic_graphrag.llm.circuit.time.time", lambda: now[0])
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10)
    breaker.record_failure()
    now[0] += 11
    assert breaker.acquire()

    # The request is still running; cooldown is not a request timeout.
    now[0] += 60
    assert not breaker.allow()
    assert not breaker.acquire()


def test_failed_half_open_probe_reopens_after_failure_window_expired(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("agentic_graphrag.llm.circuit.time.time", lambda: now[0])
    breaker = CircuitBreaker(
        failure_threshold=99, cooldown_seconds=10, window_seconds=5, min_calls=2
    )
    breaker.record_success()
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    now[0] += 11
    assert breaker.acquire()

    breaker.record_failure()

    assert breaker.state is CircuitState.OPEN
    assert not breaker.allow()


def test_late_success_from_before_an_outage_does_not_close_the_breaker(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    llm = LLMProvider(api_key="stub", circuit=CircuitBreaker(1, 60))

    def post(_path: str, payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        if payload["messages"][0]["content"] == "slow":
            started.set()
            assert release.wait(timeout=5)
            return _reply()
        raise httpx.ConnectError("upstream unavailable")

    monkeypatch.setattr("agentic_graphrag.llm.provider.post_json", post)
    with ThreadPoolExecutor(max_workers=1) as pool:
        slow = pool.submit(llm.complete, [Message(role="user", content="slow")])
        try:
            assert started.wait(timeout=5)
            with pytest.raises(httpx.ConnectError):
                llm.complete([Message(role="user", content="fail")])
            assert llm.circuits.route(ROUTE_STRONG).state is CircuitState.OPEN
        finally:
            release.set()
        assert slow.result(timeout=5) == "ok"

    assert llm.circuits.route(ROUTE_STRONG).state is CircuitState.OPEN


def test_cancelling_a_probe_allows_a_new_probe_without_waiting(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("agentic_graphrag.llm.circuit.time.time", lambda: now[0])
    budget = BudgetTracker(max_llm_calls=1)
    llm = LLMProvider(api_key="stub", budget=budget, circuit=CircuitBreaker(1, 10))
    breaker = llm.circuits.route(ROUTE_STRONG)
    breaker.record_failure()
    now[0] += 11

    def cancel(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise asyncio.CancelledError

    monkeypatch.setattr("agentic_graphrag.llm.provider.post_json", cancel)
    with pytest.raises(asyncio.CancelledError):
        llm.complete([Message(role="user", content="q")])

    assert breaker.allow()
    assert budget.llm_calls == 0
    monkeypatch.setattr("agentic_graphrag.llm.provider.post_json", lambda *_a, **_kw: _reply())
    assert llm.complete([Message(role="user", content="q")]) == "ok"
    assert breaker.state is CircuitState.CLOSED
