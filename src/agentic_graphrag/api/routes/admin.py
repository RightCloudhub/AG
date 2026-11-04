"""Admin & observability endpoints (ENT-02 / ENT-08).

Includes:
  GET  /v1/traces/{query_id}   — query trace lookup (admin)
  GET  /v1/budget/snapshot      — multi-level budget state (admin)
  GET  /v1/audit-events         — security event log (admin)
  GET  /metrics-prom            — Prometheus text exposition (public)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Query, Request

from agentic_graphrag.api.envelope import MetaBody, ok
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.deps import service
from agentic_graphrag.api.tenant import request_tenant
from agentic_graphrag.app.errors import INVALID_INPUT, ApiError
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.tenancy import tenant_matches
from agentic_graphrag.llm.circuit import CircuitState, shared_breaker_states

router = APIRouter(prefix="/v1", tags=["admin"])

# Prometheus content type per OpenMetrics spec
_PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


# ---------------------------------------------------------------------------
# ENT-02: troubleshooting closure
# ---------------------------------------------------------------------------


@router.get("/traces/{query_id}", dependencies=[Depends(require_role(Role.ADMIN))])
def get_trace(query_id: str, request: Request) -> JsonObject:
    """Return trace spans for a given query_id (admin only)."""
    from agentic_graphrag.observability.trace import get_tracer

    ctx = get_tracer().get(query_id)
    if ctx is None or not tenant_matches(ctx.tenant_id, request_tenant(request)):
        raise ApiError(INVALID_INPUT, f"Unknown query_id: {query_id}", status_code=404)
    return ok(ctx.to_dict())


@router.get("/budget/snapshot", dependencies=[Depends(require_role(Role.ADMIN))])
def budget_snapshot(request: Request) -> JsonObject:
    """Multi-level budget usage snapshot (admin only)."""
    svc = service(request)
    if svc.multi_budget is None:
        return ok({})
    return ok(svc.multi_budget.snapshot(tenant_id=request_tenant(request)))


@dataclass
class AuditEventQuery:
    since: float | None = None
    until: float | None = None
    tenant_id: str | None = None
    action: str | None = None
    limit: int = 200


def _audit_event_query(
    *,
    since: float | None = Query(None),
    until: float | None = Query(None),
    tenant_id: str | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
) -> AuditEventQuery:
    return AuditEventQuery(since, until, tenant_id, action, limit)


@router.get("/audit-events", dependencies=[Depends(require_role(Role.ADMIN))])
def list_audit_events(
    request: Request, q: Annotated[AuditEventQuery, Depends(_audit_event_query)]
) -> JsonObject:
    """List security/management audit events (admin only)."""
    from agentic_graphrag.governance.audit_events import get_audit_event_store

    store = get_audit_event_store()
    tenant_id = request_tenant(request)
    if q.tenant_id is not None and q.tenant_id != tenant_id:
        raise ApiError(INVALID_INPUT, "Tenant not found", status_code=404)
    events = store.list_events(
        since=q.since,
        until=q.until,
        tenant_id=tenant_id,
        action=q.action,
        limit=q.limit,
    )
    return ok(
        [e.to_dict() for e in events],
        meta=MetaBody(total=len(events), limit=q.limit),
    )


# ---------------------------------------------------------------------------
# ENT-08: Prometheus text exposition
# ---------------------------------------------------------------------------


def _prom_line(name: str, value: Any, labels: dict[str, str] | None = None) -> str:
    """Format a single Prometheus metric line."""
    if labels:
        lbl = ",".join(f'{k}="{v}"' for k, v in labels.items())
        return f"{name}{{{lbl}}} {value}"
    return f"{name} {value}"


_LATENCY_GAUGES = (
    ("agr_latency_p50_ms", "Query latency P50 in milliseconds", "latency_p50_ms"),
    ("agr_latency_p95_ms", "Query latency P95 in milliseconds", "latency_p95_ms"),
    ("agr_latency_p99_ms", "Query latency P99 in milliseconds", "latency_p99_ms"),
)
_LABELLED_FAMILIES = (
    ("agr_route_queries_total", "Queries by route", "route_counts", "route"),
    ("agr_errors_total", "Errors by code", "error_counts", "code"),
    (
        "agr_event_handler_failures_total",
        "Critical event handlers that failed after retries (alert on increase)",
        "event_handler_failures",
        "handler",
    ),
    (
        "agr_llm_calls_total",
        "LLM and embedding calls by role, cache replays included",
        "llm_role_calls",
        "role",
    ),
    (
        "agr_llm_remote_ms_total",
        "Milliseconds spent waiting on the LLM gateway by role",
        "llm_role_remote_ms",
        "role",
    ),
)


def prometheus_metrics_text(*, tenant_id: str | None = None) -> str:
    """Convert MetricsRegistry summary to Prometheus exposition format."""
    from agentic_graphrag.observability.metrics import get_metrics

    s = cast(Mapping[str, Any], get_metrics().summary(tenant_id=tenant_id))
    lines: list[str] = []
    _single(lines, "agr_queries_total", "Total queries processed", kind="counter", value=s["count"])
    for name, help_text, key in _LATENCY_GAUGES:
        _single(lines, name, help_text, kind="gauge", value=s[key])
    for name, help_text, key, label in _LABELLED_FAMILIES:
        _labelled(lines, name, help_text, counts=s.get(key), label=label)
    _degradation_family(lines, s.get("degradations"))
    _latency_histogram_family(lines, s.get("latency_histogram"))
    _llm_circuit_family(lines)
    _single(
        lines,
        "agr_budget_trips_total",
        "Budget trip events",
        kind="counter",
        value=s["budget_trips"],
    )
    return "\n".join(lines) + "\n"


def _single(lines: list[str], name: str, help_text: str, *, kind: str, value: Any) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {kind}")
    lines.append(_prom_line(name, value))
    lines.append("")


def _latency_histogram_family(lines: list[str], rows: Mapping[str, Any] | None) -> None:
    """``agr_query_latency_ms``: aggregatable per-route latency (AC-4 has per-route targets)."""
    if not rows:
        return
    lines.append("# HELP agr_query_latency_ms Query latency in milliseconds by route")
    lines.append("# TYPE agr_query_latency_ms histogram")
    for route, row in sorted(rows.items()):
        for edge, count in row["buckets"].items():
            labels = {"route": route, "le": edge}
            lines.append(_prom_line("agr_query_latency_ms_bucket", count, labels))
        labels = {"route": route, "le": "+Inf"}
        lines.append(_prom_line("agr_query_latency_ms_bucket", row["count"], labels))
        lines.append(_prom_line("agr_query_latency_ms_sum", row["sum_ms"], {"route": route}))
        lines.append(_prom_line("agr_query_latency_ms_count", row["count"], {"route": route}))
    lines.append("")


def _llm_circuit_family(lines: list[str]) -> None:
    """``agr_llm_circuit_open{route}``: 1 while calls on the route are refused."""
    states = shared_breaker_states()
    if not states:
        return
    lines.append("# HELP agr_llm_circuit_open LLM circuit breaker open on this route")
    lines.append("# TYPE agr_llm_circuit_open gauge")
    for route, state in sorted(states.items()):
        is_open = int(state == CircuitState.OPEN)
        lines.append(_prom_line("agr_llm_circuit_open", is_open, {"route": route}))
    lines.append("")


def _degradation_family(lines: list[str], rows: Sequence[Mapping[str, Any]] | None) -> None:
    """``agr_degradation_total{component,reason}`` — the two-label degradation counter (O-9)."""
    if not rows:
        return
    lines.append("# HELP agr_degradation_total Degraded dependency events by component and reason")
    lines.append("# TYPE agr_degradation_total counter")
    for row in rows:
        lines.append(
            _prom_line(
                "agr_degradation_total",
                row.get("count", 0),
                {"component": row.get("component", ""), "reason": row.get("reason", "")},
            )
        )
    lines.append("")


def _labelled(
    lines: list[str], name: str, help_text: str, *, counts: Mapping[str, int] | None, label: str
) -> None:
    """Append one labelled counter family: (name, help, {label_value: count}, label)."""
    if not counts:
        return
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} counter")
    for key, count in sorted(counts.items()):
        lines.append(_prom_line(name, count, {label: key}))
    lines.append("")
