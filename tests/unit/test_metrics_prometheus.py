"""Metrics summary enrichment + prometheus text."""

from __future__ import annotations

from agentic_graphrag.observability.metrics import MetricsRegistry, QueryMetrics


def test_summary_has_latency_by_route_and_prometheus():
    reg = MetricsRegistry()
    reg.record(
        QueryMetrics(query_id="1", route="agentic", latency_ms=100, status="answered")
    )
    reg.record(
        QueryMetrics(query_id="2", route="fast_path", latency_ms=20, status="answered")
    )
    reg.record(
        QueryMetrics(
            query_id="3", route="agentic", latency_ms=200, status="error", error_code="X"
        )
    )
    s = reg.summary()
    assert "agentic" in s["latency_by_route"]
    assert "fast_path" in s["latency_by_route"]
    assert s["recent_error_rate"] > 0
    text = reg.prometheus_text()
    assert "agr_queries_total" in text
    assert "agr_latency_p95_ms" in text
