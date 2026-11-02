"""Query-level metrics collection (FR-OP-01 / P3-OP-01)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, TypedDict

from agentic_graphrag.core.enums import QueryRoute
from agentic_graphrag.core.tenancy import tenant_matches


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
        }


class MetricsRegistry:
    """In-process metrics store; production can export to Prometheus later."""

    def __init__(self, *, max_events: int = 5000) -> None:
        self.max_events = max_events
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._latency: list[int] = []
        self._hops: list[int] = []
        self._route_counts: dict[str, int] = defaultdict(int)
        self._error_counts: dict[str, int] = defaultdict(int)
        self._budget_trips = 0
        self._event_failures: dict[str, int] = defaultdict(int)
        self._degradations: dict[tuple[str, str], int] = defaultdict(int)
        self._tenants: dict[str, _TenantMetrics] = {}

    def record(self, m: QueryMetrics) -> None:
        with self._lock:
            self._events.append(m.to_dict())
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events :]
            self._latency.append(m.latency_ms)
            self._hops.append(m.hops)
            self._route_counts[m.route] += 1
            if m.error_code:
                self._error_counts[m.error_code] += 1
            if m.error_code in {"BUDGET_EXCEEDED"}:
                self._budget_trips += 1
            self._tenant(m.tenant_id).record(m)

    def _tenant(self, tenant_id: str) -> _TenantMetrics:
        return self._tenants.setdefault(tenant_id or "default", _TenantMetrics())

    def record_event_failure(self, key: str, *, tenant_id: str = "") -> None:
        """A critical event handler failed after retries (O-4: alert on > 0)."""
        with self._lock:
            self._event_failures[key] += 1
            self._tenant(tenant_id).failures[key] += 1

    def record_budget_trip(self, *, tenant_id: str = "") -> None:
        with self._lock:
            self._budget_trips += 1
            self._tenant(tenant_id).budget_trips += 1

    def record_degradation(self, *, component: str, reason: str, tenant_id: str = "") -> None:
        """One degraded dependency event (O-9): ``agr_degradation_total{component,reason}``."""
        with self._lock:
            self._degradations[(component, reason)] += 1
            self._tenant(tenant_id).degradations[(component, reason)] += 1

    @staticmethod
    def _degradation_rows(counts: dict[tuple[str, str], int]) -> list[DegradationRow]:
        return [
            {"component": component, "reason": reason, "count": count}
            for (component, reason), count in sorted(counts.items())
        ]

    def percentile(self, p: float, values: list[int] | None = None) -> float:
        data = sorted(values if values is not None else self._latency)
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
            if tenant_id is not None:
                return self._tenant_summary(tenant_id)
            lat = list(self._latency)
            hops = list(self._hops)
            return {
                "count": len(self._events),
                "latency_p50_ms": self.percentile(50, lat),
                "latency_p95_ms": self.percentile(95, lat),
                "latency_p99_ms": self.percentile(99, lat),
                "hops_avg": (sum(hops) / len(hops)) if hops else 0.0,
                "route_counts": dict(self._route_counts),
                "error_counts": dict(self._error_counts),
                "budget_trips": self._budget_trips,
                "event_handler_failures": dict(self._event_failures),
                "degradations": self._degradation_rows(dict(self._degradations)),
            }

    def _tenant_summary(self, tenant_id: str) -> MetricsSummary:
        events = [event for event in self._events if tenant_matches(event["tenant_id"], tenant_id)]
        totals = self._tenants.get(tenant_id or "default", _TenantMetrics())
        lat = [event["latency_ms"] for event in events]
        hops = [event["hops"] for event in events]
        return {
            "count": totals.count,
            "latency_p50_ms": self.percentile(50, lat),
            "latency_p95_ms": self.percentile(95, lat),
            "latency_p99_ms": self.percentile(99, lat),
            "hops_avg": sum(hops) / len(hops) if hops else 0.0,
            "route_counts": dict(totals.routes),
            "error_counts": dict(totals.errors),
            "budget_trips": totals.budget_trips,
            "event_handler_failures": dict(totals.failures),
            "degradations": self._degradation_rows(dict(totals.degradations)),
        }

    def recent(self, n: int = 50, *, tenant_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self._events if tenant_matches(e["tenant_id"], tenant_id)][-n:]


@dataclass
class _TenantMetrics:
    count: int = 0
    routes: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    errors: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    failures: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    degradations: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))
    budget_trips: int = 0

    def record(self, value: QueryMetrics) -> None:
        self.count += 1
        self.routes[value.route] += 1
        if value.error_code:
            self.errors[value.error_code] += 1
        if value.error_code == "BUDGET_EXCEEDED":
            self.budget_trips += 1


_GLOBAL = MetricsRegistry()


def get_metrics() -> MetricsRegistry:
    return _GLOBAL
