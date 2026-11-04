"""Query-level metrics collection (FR-OP-01 / P3-OP-01)."""

from __future__ import annotations

import threading
import time
from bisect import bisect_left
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Final, TypedDict

from agentic_graphrag.core.enums import QueryRoute
from agentic_graphrag.core.tenancy import tenant_matches

# Edges around the AC-4 targets (3 s fast path, 8 s agentic) and the 45 s query timeout.
LATENCY_BUCKETS_MS: Final = (
    100,
    250,
    500,
    1_000,
    2_000,
    3_000,
    5_000,
    8_000,
    15_000,
    30_000,
    45_000,
    60_000,
    120_000,
)


class MetricsSummary(TypedDict):
    count: int
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    hops_avg: float
    route_counts: dict[str, int]
    error_counts: dict[str, int]
    budget_trips: int
    event_handler_failures: dict[str, int]
    degradations: list[DegradationRow]
    latency_histogram: dict[str, HistogramRow]
    llm_role_calls: dict[str, int]
    llm_role_remote_ms: dict[str, int]


class HistogramRow(TypedDict):
    """Cumulative latency buckets of one route, as Prometheus expects them."""

    buckets: dict[str, int]
    count: int
    sum_ms: int


class DegradationRow(TypedDict):
    """One ``agr_degradation_total{component,reason}`` sample (O-9)."""

    component: str
    reason: str
    count: int


@dataclass
class QueryMetrics:
    query_id: str
    route: QueryRoute = QueryRoute.AGENTIC
    hops: int = 0
    llm_calls: int = 0
    tokens: int = 0
    tool_calls: int = 0
    latency_ms: int = 0
    status: str = ""
    cost_units: float = 0.0
    tenant_id: str = ""
    user_id: str = ""
    error_code: str = ""
    started_at: float = field(default_factory=time.time)
    tool_breakdown: dict[str, int] = field(default_factory=dict)
    llm_by_role: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "route": self.route,
            "hops": self.hops,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
            "tool_calls": self.tool_calls,
            "latency_ms": self.latency_ms,
            "status": self.status,
            "cost_units": self.cost_units,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "error_code": self.error_code,
            "tool_breakdown": dict(self.tool_breakdown),
            "llm_by_role": dict(self.llm_by_role),
        }


@dataclass
class _Histogram:
    """Per-bucket counts; the last slot holds observations above every edge."""

    counts: list[int] = field(default_factory=lambda: [0] * (len(LATENCY_BUCKETS_MS) + 1))
    count: int = 0
    sum_ms: int = 0

    def observe(self, value: int) -> None:
        self.counts[bisect_left(LATENCY_BUCKETS_MS, value)] += 1
        self.count += 1
        self.sum_ms += value

    def row(self) -> HistogramRow:
        buckets: dict[str, int] = {}
        running = 0
        for edge, count in zip(LATENCY_BUCKETS_MS, self.counts, strict=False):
            running += count
            buckets[str(edge)] = running
        return {"buckets": buckets, "count": self.count, "sum_ms": self.sum_ms}


@dataclass
class _Totals:
    """Monotonic counters, kept for the whole process and for each tenant."""

    count: int = 0
    routes: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    errors: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    failures: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    degradations: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))
    budget_trips: int = 0
    latency: dict[str, _Histogram] = field(default_factory=lambda: defaultdict(_Histogram))
    role_calls: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    role_remote_ms: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def record(self, value: QueryMetrics) -> None:
        self.count += 1
        self.routes[value.route] += 1
        if value.error_code:
            self.errors[value.error_code] += 1
        if value.error_code == "BUDGET_EXCEEDED":
            self.budget_trips += 1
        self.latency[str(value.route)].observe(value.latency_ms)
        for role, usage in value.llm_by_role.items():
            self.role_calls[role] += usage.get("calls", 0)
            self.role_remote_ms[role] += usage.get("llm_ms", 0)


class MetricsRegistry:
    """In-process metrics store; production can export to Prometheus later.

    Counters never saturate. Percentiles describe the last ``max_events`` queries,
    so a burst of slow queries shows up however long the process has been running.
    """

    def __init__(self, *, max_events: int = 5000) -> None:
        self.max_events = max_events
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._totals = _Totals()
        self._tenants: dict[str, _Totals] = {}

    def record(self, m: QueryMetrics) -> None:
        with self._lock:
            self._events.append(m.to_dict())
            self._totals.record(m)
            self._tenant(m.tenant_id).record(m)

    def _tenant(self, tenant_id: str) -> _Totals:
        return self._tenants.setdefault(tenant_id or "default", _Totals())

    def record_event_failure(self, key: str, *, tenant_id: str = "") -> None:
        """A critical event handler failed after retries (O-4: alert on > 0)."""
        with self._lock:
            self._totals.failures[key] += 1
            self._tenant(tenant_id).failures[key] += 1

    def record_budget_trip(self, *, tenant_id: str = "") -> None:
        with self._lock:
            self._totals.budget_trips += 1
            self._tenant(tenant_id).budget_trips += 1

    def record_degradation(self, *, component: str, reason: str, tenant_id: str = "") -> None:
        """One degraded dependency event (O-9): ``agr_degradation_total{component,reason}``."""
        with self._lock:
            self._totals.degradations[(component, reason)] += 1
            self._tenant(tenant_id).degradations[(component, reason)] += 1

    @staticmethod
    def _degradation_rows(counts: dict[tuple[str, str], int]) -> list[DegradationRow]:
        return [
            {"component": component, "reason": reason, "count": count}
            for (component, reason), count in sorted(counts.items())
        ]

    def percentile(self, p: float, values: list[int] | None = None) -> float:
        samples = values if values is not None else [e["latency_ms"] for e in self._events]
        data = sorted(samples)
        if not data:
            return 0.0
        if p <= 0:
            return float(data[0])
        if p >= 100:
            return float(data[-1])
        idx = int(round((p / 100.0) * (len(data) - 1)))
        return float(data[idx])

    def summary(self, *, tenant_id: str | None = None) -> MetricsSummary:
        with self._lock:
            if tenant_id is None:
                return self._summarize(self._totals, list(self._events))
            events = [e for e in self._events if tenant_matches(e["tenant_id"], tenant_id)]
            return self._summarize(self._tenants.get(tenant_id or "default", _Totals()), events)

    def _summarize(self, totals: _Totals, events: list[dict[str, Any]]) -> MetricsSummary:
        latencies = [event["latency_ms"] for event in events]
        hops = [event["hops"] for event in events]
        return {
            "count": totals.count,
            "latency_p50_ms": self.percentile(50, latencies),
            "latency_p95_ms": self.percentile(95, latencies),
            "latency_p99_ms": self.percentile(99, latencies),
            "hops_avg": sum(hops) / len(hops) if hops else 0.0,
            "route_counts": dict(totals.routes),
            "error_counts": dict(totals.errors),
            "budget_trips": totals.budget_trips,
            "event_handler_failures": dict(totals.failures),
            "degradations": self._degradation_rows(dict(totals.degradations)),
            "latency_histogram": {route: h.row() for route, h in totals.latency.items()},
            "llm_role_calls": dict(totals.role_calls),
            "llm_role_remote_ms": dict(totals.role_remote_ms),
        }

    def recent(self, n: int = 50, *, tenant_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self._events if tenant_matches(e["tenant_id"], tenant_id)][-n:]


_GLOBAL = MetricsRegistry()


def get_metrics() -> MetricsRegistry:
    return _GLOBAL
