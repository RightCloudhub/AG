"""Query metrics, tracing, audit, structured logging, and redaction."""

from agentic_graphrag.observability.audit_events import (
    AuditEvent,
    AuditEventStore,
    emit_audit_event,
    get_audit_event_store,
)
from agentic_graphrag.observability.logging_setup import (
    bind_context,
    clear_context,
    get_logger,
    setup_logging,
)
from agentic_graphrag.observability.metrics import MetricsRegistry, QueryMetrics, get_metrics
from agentic_graphrag.observability.otel_bridge import otel_span, setup_otel
from agentic_graphrag.observability.redaction import Redactor, get_redactor, redact_log_record
from agentic_graphrag.observability.trace import TraceContext, get_tracer, span

__all__ = [
    "AuditEvent",
    "AuditEventStore",
    "MetricsRegistry",
    "QueryMetrics",
    "Redactor",
    "TraceContext",
    "bind_context",
    "clear_context",
    "emit_audit_event",
    "get_audit_event_store",
    "get_logger",
    "get_metrics",
    "get_redactor",
    "get_tracer",
    "otel_span",
    "redact_log_record",
    "setup_logging",
    "setup_otel",
    "span",
]
