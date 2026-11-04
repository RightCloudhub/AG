"""Metrics stay truthful after long uptime and split by route and LLM role (OBS-07, GOV-7)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.routes import admin
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.app.telemetry import record_metrics
from agentic_graphrag.core.enums import QueryRoute
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.budget import LLM_BY_ROLE_KEY
from agentic_graphrag.llm.circuit import (
    ROUTE_STRONG,
    CircuitBreaker,
    shared_breakers,
)
from agentic_graphrag.observability import metrics
from agentic_graphrag.observability.metrics import MetricsRegistry, QueryMetrics

MAX_EVENTS = 20
FAST_MS = 100
SLOW_MS = 60_000


def _query(**overrides) -> QueryMetrics:
    return QueryMetrics(**{"query_id": "q", "latency_ms": FAST_MS, **overrides})


@pytest.fixture
def registry(monkeypatch) -> MetricsRegistry:
    fresh = MetricsRegistry(max_events=MAX_EVENTS)
    monkeypatch.setattr(metrics, "_GLOBAL", fresh)
    return fresh


def test_query_count_keeps_counting_past_the_event_window() -> None:
    registry = MetricsRegistry(max_events=MAX_EVENTS)
    for _ in range(MAX_EVENTS * 5):
        registry.record(_query())

    assert registry.summary()["count"] == MAX_EVENTS * 5
    assert len(registry.recent(n=MAX_EVENTS * 5)) == MAX_EVENTS


def test_percentiles_reflect_recent_queries_not_the_whole_uptime() -> None:
    registry = MetricsRegistry(max_events=MAX_EVENTS)
    for _ in range(1000):
        registry.record(_query())
    for _ in range(MAX_EVENTS):
        registry.record(_query(latency_ms=SLOW_MS))

    assert registry.summary()["latency_p95_ms"] == SLOW_MS


def test_tenant_summary_has_the_same_shape_as_the_global_one() -> None:
    registry = MetricsRegistry(max_events=MAX_EVENTS)
    registry.record(_query(tenant_id="alpha", route=QueryRoute.FAST_PATH))
    registry.record(_query(tenant_id="beta"))
    registry.record_budget_trip(tenant_id="alpha")

    whole, alpha = registry.summary(), registry.summary(tenant_id="alpha")

    assert set(whole) == set(alpha)
    assert whole["count"] == 2 and alpha["count"] == 1
    assert whole["budget_trips"] == 1 and alpha["budget_trips"] == 1
    assert set(alpha["latency_histogram"]) == {"fast_path"}


def test_latency_histogram_is_cumulative_per_route() -> None:
    registry = MetricsRegistry(max_events=MAX_EVENTS)
    registry.record(_query(route=QueryRoute.AGENTIC, latency_ms=4_000))
    registry.record(_query(route=QueryRoute.AGENTIC, latency_ms=SLOW_MS * 10))
    registry.record(_query(route=QueryRoute.FAST_PATH, latency_ms=FAST_MS))

    histogram = registry.summary()["latency_histogram"]

    agentic = histogram["agentic"]
    assert agentic["count"] == 2
    assert agentic["sum_ms"] == 4_000 + SLOW_MS * 10
    assert agentic["buckets"]["3000"] == 0
    assert agentic["buckets"]["8000"] == 1
    assert agentic["buckets"]["120000"] == 1
    assert histogram["fast_path"]["buckets"]["3000"] == 1


def test_role_usage_is_aggregated() -> None:
    registry = MetricsRegistry(max_events=MAX_EVENTS)
    usage = {"planner": {"calls": 1, "llm_ms": 30}, "critic": {"calls": 2, "llm_ms": 40}}
    registry.record(_query(llm_by_role=usage))
    registry.record(_query(llm_by_role={"critic": {"calls": 1, "llm_ms": 5}}))

    summary = registry.summary()

    assert summary["llm_role_calls"] == {"planner": 1, "critic": 3}
    assert summary["llm_role_remote_ms"] == {"planner": 30, "critic": 45}


def test_record_metrics_forwards_role_usage(registry) -> None:
    chain = ReasoningChain(question="q")
    chain.metadata = {LLM_BY_ROLE_KEY: {"composer": {"calls": 1, "llm_ms": 9}}}

    record_metrics(chain, tenant_id="alpha", user_id="u")

    assert registry.summary()["llm_role_calls"] == {"composer": 1}


def test_prometheus_exports_histogram_and_role_counters(registry) -> None:
    registry.record(
        _query(route=QueryRoute.AGENTIC, llm_by_role={"critic": {"calls": 2, "llm_ms": 40}})
    )

    text = admin.prometheus_metrics_text()

    assert "# TYPE agr_query_latency_ms histogram" in text
    assert 'agr_query_latency_ms_bucket{route="agentic",le="100"} 1' in text
    assert 'agr_query_latency_ms_bucket{route="agentic",le="+Inf"} 1' in text
    assert f'agr_query_latency_ms_sum{{route="agentic"}} {FAST_MS}' in text
    assert 'agr_query_latency_ms_count{route="agentic"} 1' in text
    assert 'agr_llm_calls_total{role="critic"} 2' in text
    assert 'agr_llm_remote_ms_total{role="critic"} 40' in text


def test_prometheus_counter_does_not_saturate(registry) -> None:
    for _ in range(MAX_EVENTS + 5):
        registry.record(_query())
    assert f"agr_queries_total {MAX_EVENTS + 5}" in admin.prometheus_metrics_text()


def test_prometheus_exports_llm_circuit_state(registry) -> None:
    breakers = shared_breakers(("endpoint",), CircuitBreaker(1, 60))
    breakers.route(ROUTE_STRONG).record_failure()

    text = admin.prometheus_metrics_text()

    assert f'agr_llm_circuit_open{{route="{ROUTE_STRONG}"}} 1' in text


def test_healthz_reports_llm_circuit() -> None:
    service = QueryService.create_offline()
    client = TestClient(create_app(query_service=service))
    try:
        assert client.get("/healthz").json()["checks"]["llm_circuit"] == "closed"

        shared_breakers(("endpoint",), CircuitBreaker(1, 60)).route(ROUTE_STRONG).record_failure()

        checks = client.get("/healthz").json()["checks"]
        assert checks["llm_circuit"] == f"{ROUTE_STRONG}=open"
    finally:
        service.close()
