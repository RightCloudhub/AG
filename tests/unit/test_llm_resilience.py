"""Per-route breakers, sliding-window rule and STRONG→LIGHT fallback (O-8)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from agentic_graphrag.app.degradation_policy import (
    DEGRADED_CHANNELS_KEY,
    apply_degradation_policy,
)
from agentic_graphrag.config import AppConfig, Settings, build_llm_provider
from agentic_graphrag.config_llm import LLMResilienceConfig
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.budget import DEGRADED_FROM_KEY, BudgetTracker, apply_budget
from agentic_graphrag.llm.circuit import (
    ROUTE_EMBED,
    ROUTE_LIGHT,
    ROUTE_STRONG,
    CircuitBreaker,
    CircuitBreakerSet,
    CircuitState,
    note_call_failure,
    shared_breaker_states,
)
from agentic_graphrag.llm.provider import LLMProvider, Message, Tier
from agentic_graphrag.observability.degradation import ChannelHealth
from agentic_graphrag.observability.metrics import get_metrics

_REAL_CLIENT = httpx.Client


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
    assert p.circuit_for(Tier.STRONG) is p.circuits.route(ROUTE_LIGHT)
    assert p.complete(_msgs(), tier=Tier.STRONG) == "ok"
    assert p.models[-1] == "gpt-4.1-mini" and p.degradations == 1


def test_no_fallback_by_default_and_light_unaffected() -> None:
    p = _Provider({"gpt-4.1"})
    with pytest.raises(RuntimeError):
        p.complete(_msgs(), tier=Tier.STRONG)
    with pytest.raises(RuntimeError, match="circuit open"):
        p.complete(_msgs(), tier=Tier.STRONG)
    assert p.complete(_msgs(), tier=Tier.LIGHT) == "ok"  # V1: LIGHT was blocked too
    assert not p.circuit_for(Tier.STRONG).allow()
    assert p.circuit_for(Tier.LIGHT).allow()


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


def _template(**kwargs: Any) -> CircuitBreaker:
    return CircuitBreaker(**{"failure_threshold": 1, "cooldown_seconds": 60.0, **kwargs})


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://gateway.test/v1/chat/completions")
    return httpx.HTTPStatusError(
        "rejected", request=request, response=httpx.Response(status, request=request)
    )


def test_half_open_admits_one_probe_at_a_time(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("agentic_graphrag.llm.circuit.time.time", lambda: now[0])
    cb = _template(cooldown_seconds=10.0)
    cb.record_failure()
    assert not cb.acquire()
    now[0] += 11

    assert cb.allow()  # Reporting reserves nothing.
    assert cb.allow()
    assert cb.acquire()
    assert not cb.acquire()
    assert not cb.allow()

    cb.record_failure()
    assert not cb.acquire()
    now[0] += 11
    assert cb.acquire()
    cb.record_success()
    assert cb.state is CircuitState.CLOSED
    assert cb.acquire() and cb.acquire()


def test_released_probe_does_not_block_recovery(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("agentic_graphrag.llm.circuit.time.time", lambda: now[0])
    cb = _template(cooldown_seconds=10.0)
    cb.record_failure()
    now[0] += 11
    assert cb.acquire()
    assert not cb.acquire()
    cb.release_probe()
    assert cb.acquire()


@pytest.mark.parametrize("status", [400, 413, 422])
def test_request_errors_do_not_count_against_the_breaker(status: int) -> None:
    cb = _template()
    note_call_failure(cb, _status_error(status))
    assert cb.state is CircuitState.CLOSED


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
def test_upstream_errors_count_against_the_breaker(status: int) -> None:
    cb = _template()
    note_call_failure(cb, _status_error(status))
    assert cb.state is CircuitState.OPEN


def test_transport_errors_count_against_the_breaker() -> None:
    cb = _template()
    note_call_failure(cb, httpx.ConnectTimeout("no route"))
    assert cb.state is CircuitState.OPEN


def test_request_error_frees_the_half_open_probe(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("agentic_graphrag.llm.circuit.time.time", lambda: now[0])
    cb = _template(cooldown_seconds=10.0)
    cb.record_failure()
    now[0] += 11
    assert cb.acquire()
    note_call_failure(cb, _status_error(400))
    assert cb.acquire()


def _live_settings() -> Settings:
    return Settings.model_construct(
        llm_api_key="k",
        llm_base_url="https://gateway.test/v1",
        llm_embedding_base_url=None,
        llm_embedding_api_key=None,
    )


def _failing_client(calls: list[str]) -> Any:
    def client(**kwargs: Any) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(503, json={"error": "down"})

        return _REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    return client


def test_second_query_makes_no_attempt_on_an_open_route(monkeypatch) -> None:
    """Providers are per query; the breaker must still remember the outage."""
    calls: list[str] = []
    monkeypatch.setattr(httpx, "Client", _failing_client(calls))
    cfg = AppConfig()
    cfg.llm.resilience = LLMResilienceConfig(failure_threshold=1, cooldown_seconds=60)
    first = build_llm_provider(cfg=cfg, settings=_live_settings())
    second = build_llm_provider(cfg=cfg, settings=_live_settings())

    with pytest.raises(httpx.HTTPStatusError):
        first.complete(_msgs(), tier=Tier.STRONG)
    assert len(calls) == 1

    with pytest.raises(RuntimeError, match="circuit open"):
        second.complete(_msgs(), tier=Tier.STRONG)
    assert len(calls) == 1
    assert shared_breaker_states()[ROUTE_STRONG] == "open"


def test_open_strong_route_does_not_block_light_answers(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "Client", _failing_client([]))
    cfg = AppConfig()
    cfg.llm.resilience = LLMResilienceConfig(failure_threshold=1, cooldown_seconds=60)
    llm = build_llm_provider(cfg=cfg, settings=_live_settings())
    with pytest.raises(httpx.HTTPStatusError):
        llm.complete(_msgs(), tier=Tier.STRONG)

    assert not llm.circuit_for(Tier.STRONG).allow()
    assert llm.circuit_for(Tier.LIGHT).allow()


def test_verifier_breakers_are_shared_but_separate_from_the_proposer(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "Client", _failing_client([]))
    cfg = AppConfig()
    cfg.llm.resilience = LLMResilienceConfig(failure_threshold=1, cooldown_seconds=60)
    llm = build_llm_provider(cfg=cfg, settings=_live_settings())
    verifier = llm.for_verifier(model="judge")
    again = build_llm_provider(cfg=cfg, settings=_live_settings()).for_verifier(model="judge")
    assert verifier.circuits is again.circuits
    assert verifier.circuits is not llm.circuits
    with pytest.raises(httpx.HTTPStatusError):
        verifier.complete(_msgs())
    assert not again.circuit_for(Tier.STRONG).allow()
    assert llm.circuit_for(Tier.STRONG).allow()


def test_provider_without_shared_breakers_keeps_its_own(monkeypatch) -> None:
    llm = LLMProvider(api_key="k", circuit=_template())
    assert llm.for_verifier(model="judge").circuits is not llm.circuits
    assert llm.circuits.scope is None
    assert shared_breaker_states() == {}


def test_tier_fallback_is_recorded_and_reported() -> None:
    budget = BudgetTracker()
    p = _Provider({"gpt-4.1"}, fallback_to_light=True, budget=budget)
    before = _degradation_count()
    with pytest.raises(RuntimeError):
        p.complete(_msgs(), tier=Tier.STRONG)

    assert p.complete(_msgs(), tier=Tier.STRONG) == "ok"

    assert _degradation_count() == before + 1
    chain = ReasoningChain(question="q")
    apply_budget(chain, budget)
    assert chain.metadata[DEGRADED_FROM_KEY] == "strong"
    health_rows = merged_llm_rows(chain)
    assert health_rows == [{"channel": "llm", "reason": "tier_fallback", "status": "degraded"}]


def _degradation_count() -> int:
    rows = get_metrics().summary()["degradations"]
    return sum(
        row["count"]
        for row in rows
        if (row["component"], row["reason"]) == ("llm", "tier_fallback")
    )


def merged_llm_rows(chain: ReasoningChain) -> list[dict[str, str]]:
    apply_degradation_policy(chain, ChannelHealth(), AppConfig())
    return chain.metadata[DEGRADED_CHANNELS_KEY]
