"""Per-route breakers, sliding-window rule and STRONG→LIGHT fallback (O-8)."""

from __future__ import annotations

from typing import Any

import pytest

from agentic_graphrag.config import AppConfig, build_llm_provider
from agentic_graphrag.config_llm import LLMResilienceConfig
from agentic_graphrag.llm.circuit import (
    ROUTE_EMBED,
    ROUTE_LIGHT,
    ROUTE_STRONG,
    CircuitBreaker,
    CircuitBreakerSet,
    CircuitState,
)
from agentic_graphrag.llm.provider import LLMProvider, Message, Tier


def test_consecutive_rule_unchanged() -> None:
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.0)
    cb.record_failure()
    assert cb.allow()
    cb.record_failure()
    assert cb.state is CircuitState.HALF_OPEN  # cooldown 0 → half-open immediately
    cb.record_success()
    assert cb.state is CircuitState.CLOSED


def test_window_failure_rate_trips_without_consecutive_run() -> None:
    cb = CircuitBreaker(failure_threshold=99, cooldown_seconds=60, window_seconds=60, min_calls=4)
    for ok in (False, True, False, True):
        cb.record_success() if ok else cb.record_failure()
    assert cb.state is CircuitState.CLOSED  # rate is only evaluated on a failure
    cb2 = CircuitBreaker(failure_threshold=99, cooldown_seconds=60, window_seconds=60, min_calls=4)
    for ok in (True, False, False, False):
        cb2.record_success() if ok else cb2.record_failure()
    assert cb2.state is CircuitState.OPEN
    cb3 = CircuitBreaker(failure_threshold=99, cooldown_seconds=60, window_seconds=60, min_calls=10)
    for _ in range(5):
        cb3.record_failure()
    assert cb3.allow()  # below min_calls


def test_breaker_set_isolates_routes() -> None:
    routes = CircuitBreakerSet(CircuitBreaker(failure_threshold=1, cooldown_seconds=60))
    routes.route(ROUTE_EMBED).record_failure()
    assert not routes.route(ROUTE_EMBED).allow()
    assert routes.route(ROUTE_STRONG).allow()
    assert routes.states() == {ROUTE_EMBED: "open", ROUTE_STRONG: "closed"}


class _Provider(LLMProvider):
    def __init__(self, fail_models: set[str], **kw: Any) -> None:
        super().__init__(api_key="k", circuit=CircuitBreaker(1, 60), **kw)
        self.fail_models = fail_models
        self.models: list[str] = []

    def _post(self, path, payload, *, base_url, api_key, key_env="", route=ROUTE_STRONG):
        breaker = self.circuits.route(route)
        if not breaker.allow():
            raise RuntimeError(f"LLM circuit open ({route})")
        self.models.append(payload["model"])
        if payload["model"] in self.fail_models:
            breaker.record_failure()
            raise RuntimeError("upstream 503")
        breaker.record_success()
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}


def _msgs() -> list[Message]:
    return [Message(role="user", content="hi")]


def test_strong_failure_degrades_to_light_when_enabled() -> None:
    p = _Provider({"gpt-4.1"}, fallback_to_light=True)
    with pytest.raises(RuntimeError):
        p.complete(_msgs(), tier=Tier.STRONG)
    assert p.circuit is p.circuits.route(ROUTE_LIGHT)
    assert p.complete(_msgs(), tier=Tier.STRONG) == "ok"
    assert p.models[-1] == "gpt-4.1-mini" and p.degradations == 1


def test_no_fallback_by_default_and_light_unaffected() -> None:
    p = _Provider({"gpt-4.1"})
    with pytest.raises(RuntimeError):
        p.complete(_msgs(), tier=Tier.STRONG)
    with pytest.raises(RuntimeError, match="circuit open"):
        p.complete(_msgs(), tier=Tier.STRONG)
    assert p.complete(_msgs(), tier=Tier.LIGHT) == "ok"  # V1: LIGHT was blocked too
    assert not p.circuit.allow()


def test_both_tiers_down_stays_on_strong() -> None:
    p = _Provider({"gpt-4.1", "gpt-4.1-mini"}, fallback_to_light=True)
    for tier in (Tier.STRONG, Tier.LIGHT):
        with pytest.raises(RuntimeError):
            p.complete(_msgs(), tier=tier)
    with pytest.raises(RuntimeError, match=r"chat:strong"):
        p.complete(_msgs(), tier=Tier.STRONG)


def test_config_builds_breaker_template() -> None:
    cfg = AppConfig()
    cfg.llm.resilience = LLMResilienceConfig(window_seconds=30, fallback_to_light=True)
    p = build_llm_provider(cfg=cfg)
    assert p.fallback_to_light is True
    assert p.circuits.template.window_seconds == 30
