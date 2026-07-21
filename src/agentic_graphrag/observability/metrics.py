"""Query-level metrics collection (FR-OP-01 / P3-OP-01)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueryMetrics:
    query_id: str
    route: str = "agentic"
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

    def record_budget_trip(self) -> None:
        with self._lock:
            self._budget_trips += 1

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

    def summary(self) -> dict[str, Any]:
        with self._lock:
            lat = list(self._latency)
            hops = list(self._hops)
            events = list(self._events)
            by_route = self._latency_by_route(events)
            recent_err = self._recent_error_rate(events, window=50)
            return {
                "count": len(events),
                "latency_p50_ms": self.percentile(50, lat),
                "latency_p95_ms": self.percentile(95, lat),
                "latency_p99_ms": self.percentile(99, lat),
                "latency_by_route": by_route,
                "hops_avg": (sum(hops) / len(hops)) if hops else 0.0,
                "route_counts": dict(self._route_counts),
                "error_counts": dict(self._error_counts),
                "budget_trips": self._budget_trips,
                "recent_error_rate": recent_err,
            }

    def recent(self, n: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events[-n:])

    def prometheus_text(self) -> str:
        """Minimal Prometheus exposition for deploy-side scrapers."""
        s = self.summary()
        lines = [
            "# HELP agr_queries_total Total recorded queries",
            "# TYPE agr_queries_total counter",
            f"agr_queries_total {s['count']}",
            "# HELP agr_latency_p95_ms Query latency P95 milliseconds",
            "# TYPE agr_latency_p95_ms gauge",
            f"agr_latency_p95_ms {s['latency_p95_ms']}",
            "# HELP agr_budget_trips_total Budget trip count",
            "# TYPE agr_budget_trips_total counter",
            f"agr_budget_trips_total {s['budget_trips']}",
            "# HELP agr_recent_error_rate Recent error rate (0-1)",
            "# TYPE agr_recent_error_rate gauge",
            f"agr_recent_error_rate {s['recent_error_rate']}",
        ]
        for route, stats in (s.get("latency_by_route") or {}).items():
            p95 = stats.get("p95_ms", 0.0)
            lines.append(
                f'agr_latency_p95_by_route_ms{{route="{route}"}} {p95}'
            )
        for code, n in (s.get("error_counts") or {}).items():
            lines.append(f'agr_errors_total{{code="{code}"}} {n}')
        return "\n".join(lines) + "\n"

    def _latency_by_route(self, events: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
        buckets: dict[str, list[int]] = defaultdict(list)
        for e in events:
            buckets[str(e.get("route") or "unknown")].append(int(e.get("latency_ms") or 0))
        out: dict[str, dict[str, float]] = {}
        for route, vals in buckets.items():
            out[route] = {
                "p50_ms": self.percentile(50, vals),
                "p95_ms": self.percentile(95, vals),
                "p99_ms": self.percentile(99, vals),
                "count": float(len(vals)),
            }
        return out

    @staticmethod
    def _recent_error_rate(events: list[dict[str, Any]], *, window: int) -> float:
        slice_ = events[-window:] if events else []
        if not slice_:
            return 0.0
        bad = sum(1 for e in slice_ if e.get("error_code") or e.get("status") == "error")
        return bad / len(slice_)


_GLOBAL = MetricsRegistry()


def get_metrics() -> MetricsRegistry:
    return _GLOBAL
