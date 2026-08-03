"""Admin & observability endpoints (ENT-02 / ENT-08).

Includes:
  GET  /v1/traces/{query_id}   — query trace lookup (admin)
  GET  /v1/budget/snapshot      — multi-level budget state (admin)
  GET  /v1/audit-events         — security event log (admin)
  GET  /metrics-prom            — Prometheus text exposition (public)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from agentic_graphrag.api.envelope import MetaBody, ok
from agentic_graphrag.api.errors import INVALID_INPUT, ApiError
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.service import QueryService

router = APIRouter(prefix="/v1", tags=["admin"])

# Prometheus content type per OpenMetrics spec
_PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _service(request: Request) -> QueryService:
    svc = getattr(request.app.state, "query_service", None)
    if svc is None:
        raise ApiError("SERVICE_UNAVAILABLE", "Query service not initialized", status_code=503)
    return svc


def _principal(request: Request) -> tuple[str, str]:
    p = getattr(request.state, "principal", None)
    if p is None:
        return "default", "anonymous"
    return p.tenant_id, p.user_id


# ---------------------------------------------------------------------------
# ENT-02: troubleshooting closure
# ---------------------------------------------------------------------------


@router.get("/traces/{query_id}", dependencies=[Depends(require_role(Role.ADMIN))])
def get_trace(query_id: str) -> dict:
    """Return trace spans for a given query_id (admin only)."""
    from agentic_graphrag.observability.trace import get_tracer

    ctx = get_tracer().get(query_id)
    if ctx is None:
        raise ApiError(INVALID_INPUT, f"Unknown query_id: {query_id}", status_code=404)
    return ok(ctx.to_dict())


@router.get("/budget/snapshot", dependencies=[Depends(require_role(Role.ADMIN))])
def budget_snapshot(request: Request) -> dict:
    """Multi-level budget usage snapshot (admin only)."""
    svc = _service(request)
    if svc.multi_budget is None:
        return ok({})
    return ok(svc.multi_budget.snapshot())


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
    limit: int = Query(200, le=1000),
) -> AuditEventQuery:
    return AuditEventQuery(since, until, tenant_id, action, limit)


@router.get("/audit-events", dependencies=[Depends(require_role(Role.ADMIN))])
def list_audit_events(q: Annotated[AuditEventQuery, Depends(_audit_event_query)]) -> dict:
    """List security/management audit events (admin only)."""
    from agentic_graphrag.observability.audit_events import get_audit_event_store

    store = get_audit_event_store()
    events = store.list_events(
        since=q.since,
        until=q.until,
        tenant_id=q.tenant_id,
        action=q.action,
        limit=q.limit,
    )
    return ok(
        [e.to_dict() for e in events],
        meta=MetaBody(total=len(events), limit=q.limit),
    )


@router.get(
    "/audit/queries/recent",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def list_recent_queries(request: Request, limit: int = Query(50, ge=1, le=200)) -> dict:
    """Recent Q&A history (summary rows) for the trial UI history view."""
    svc = _service(request)
    if svc.audit_store is None:
        return ok([], meta=MetaBody(total=0, limit=limit, page=1))
    tenant_id, _user_id = _principal(request)
    rows = svc.audit_store.list_recent(limit=limit)
    scoped = [
        r
        for r in rows
        if _chain_visible(r, tenant_id)
    ]
    return ok(scoped, meta=MetaBody(total=len(scoped), limit=limit, page=1))


def _chain_visible(row: dict[str, Any], tenant_id: str) -> bool:
    meta = row.get("metadata") or {}
    owner = meta.get("tenant_id") if isinstance(meta, dict) else None
    if owner is None:
        return tenant_id == "default"
    return owner == tenant_id


# ---------------------------------------------------------------------------
# ENT-08: Prometheus text exposition
# ---------------------------------------------------------------------------


def _prom_line(name: str, value: Any, labels: dict[str, str] | None = None) -> str:
    """Format a single Prometheus metric line."""
    if labels:
        lbl = ",".join(f'{k}="{v}"' for k, v in labels.items())
        return f"{name}{{{lbl}}} {value}"
    return f"{name} {value}"


def prometheus_metrics_text() -> str:
    """Convert MetricsRegistry summary to Prometheus exposition format."""
    from agentic_graphrag.observability.metrics import get_metrics

    s = get_metrics().summary()
    lines: list[str] = []

    lines.append("# HELP agr_queries_total Total queries processed")
    lines.append("# TYPE agr_queries_total counter")
    lines.append(_prom_line("agr_queries_total", s.get("count", 0)))
    lines.append("")

    lines.append("# HELP agr_latency_p50_ms Query latency P50 in milliseconds")
    lines.append("# TYPE agr_latency_p50_ms gauge")
    lines.append(_prom_line("agr_latency_p50_ms", s.get("latency_p50_ms", 0.0)))
    lines.append("")

    lines.append("# HELP agr_latency_p95_ms Query latency P95 in milliseconds")
    lines.append("# TYPE agr_latency_p95_ms gauge")
    lines.append(_prom_line("agr_latency_p95_ms", s.get("latency_p95_ms", 0.0)))
    lines.append("")

    lines.append("# HELP agr_latency_p99_ms Query latency P99 in milliseconds")
    lines.append("# TYPE agr_latency_p99_ms gauge")
    lines.append(_prom_line("agr_latency_p99_ms", s.get("latency_p99_ms", 0.0)))
    lines.append("")

    routes = s.get("route_counts") or {}
    if routes:
        lines.append("# HELP agr_route_queries_total Queries by route")
        lines.append("# TYPE agr_route_queries_total counter")
        for route, count in sorted(routes.items()):
            lines.append(_prom_line("agr_route_queries_total", count, {"route": route}))
        lines.append("")

    errors = s.get("error_counts") or {}
    if errors:
        lines.append("# HELP agr_errors_total Errors by code")
        lines.append("# TYPE agr_errors_total counter")
        for code, count in sorted(errors.items()):
            lines.append(_prom_line("agr_errors_total", count, {"code": code}))
        lines.append("")

    lines.append("# HELP agr_budget_trips_total Budget trip events")
    lines.append("# TYPE agr_budget_trips_total counter")
    lines.append(_prom_line("agr_budget_trips_total", s.get("budget_trips", 0)))
    lines.append("")

    return "\n".join(lines) + "\n"
