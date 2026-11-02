"""Degradation recording: one funnel for every failure a run survives (O-9).

A degraded run used to look exactly like a healthy one: the embedder answered 401,
Qdrant was down, the re-ranker timed out — and the agent still answered from
whatever channel was left. :func:`record_degradation` is the single place that
makes such a failure loud:

* one structured ``WARNING`` log line (component, reason, exception type, tenant,
  query),
* one increment of ``agr_degradation_total{component,reason}``, and
* one OpenTelemetry span event on the active span.

When the caller owns the run's :class:`ChannelHealth` (the retrievers, the
executor and the re-ranker do, because they are built per query) the failure also
marks that channel, which is what puts ``degraded_channels`` into the response.

Log hygiene: only ``component``, ``reason``, the exception **type** and the
caller-supplied ``attrs`` are logged. Exception messages are deliberately not
read here — they routinely carry request URLs, document text or credentials.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from agentic_graphrag.core.enums import RetrievalChannel
from agentic_graphrag.observability.logging_setup import (
    get_logger,
    query_id_var,
    tenant_id_var,
)
from agentic_graphrag.observability.metrics import get_metrics
from agentic_graphrag.observability.otel_bridge import otel_span_event

__all__ = [
    "CHANNELS",
    "ChannelHealth",
    "ChannelStatus",
    "DEGRADED_CHANNELS_KEY",
    "LOW_CONFIDENCE_KEY",
    "REASON_AUDIT_WRITE_FAILED",
    "REASON_CACHE_WRITE_FAILED",
    "REASON_CASE_FAILED",
    "REASON_DOC_WRITE_FAILED",
    "REASON_EMBEDDING_UNAVAILABLE",
    "REASON_EVENT_HANDLER_FAILED",
    "REASON_EXTRACT_FAILED",
    "REASON_FULLTEXT_UNAVAILABLE",
    "REASON_GRAPH_STORE_UNAVAILABLE",
    "REASON_INDEX_WRITE_FAILED",
    "REASON_LLM_UNAVAILABLE",
    "REASON_MAIL_DELIVERY_FAILED",
    "REASON_RELATION_EMBED_UNAVAILABLE",
    "REASON_RERANK_UNAVAILABLE",
    "REASON_STORE_WRITE_FAILED",
    "REASON_TOOL_FAILED",
    "REASON_UNEXPECTED",
    "REASON_VERIFIER_UNAVAILABLE",
    "degraded_channel",
    "degradation_guard",
    "merge_degraded_channels",
    "record_degradation",
]

# Chain-metadata keys that carry degradation state from the run to the response
# envelope (read by ``app.degradation_policy`` and ``app.wiring``). They live here
# so domain layers can write them without importing the application layer.
DEGRADED_CHANNELS_KEY = "degraded_channels"
LOW_CONFIDENCE_KEY = "low_confidence"


class ChannelStatus:
    """Channel health values (not an enum: callers pass them as plain strings)."""

    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


_SEVERITY: dict[str, int] = {
    ChannelStatus.OK: 0,
    ChannelStatus.DEGRADED: 1,
    ChannelStatus.FAILED: 2,
}

#: Retrieval channels a run reports on — the health vocabulary, i.e. every member
#: of :class:`RetrievalChannel` except ``FUSION`` (a candidate label, not a
#: dependency). Non-channel components (``audit_store``, ``mail`` …) are logged
#: and counted but never appear in ``degraded_channels``.
CHANNELS: frozenset[str] = frozenset(
    {
        RetrievalChannel.VECTOR,
        RetrievalChannel.FULLTEXT,
        RetrievalChannel.GRAPH,
        RetrievalChannel.RERANK,
        RetrievalChannel.EMBEDDING,
        RetrievalChannel.LLM,
    }
)

# Reason slugs. They are Prometheus label values, so the vocabulary stays closed
# and low-cardinality: a new failure mode gets a new constant here, never an
# interpolated message.
REASON_EMBEDDING_UNAVAILABLE = "embedding_unavailable"
REASON_VECTOR_STORE_UNAVAILABLE = "vector_store_unavailable"
REASON_FULLTEXT_UNAVAILABLE = "fulltext_unavailable"
REASON_GRAPH_STORE_UNAVAILABLE = "graph_store_unavailable"
REASON_RELATION_EMBED_UNAVAILABLE = "relation_embed_unavailable"
REASON_RERANK_UNAVAILABLE = "rerank_unavailable"
REASON_LLM_UNAVAILABLE = "llm_unavailable"
REASON_TOOL_FAILED = "tool_failed"
REASON_INDEX_WRITE_FAILED = "index_write_failed"
REASON_AUDIT_WRITE_FAILED = "audit_write_failed"
REASON_CACHE_WRITE_FAILED = "cache_write_failed"
REASON_DOC_WRITE_FAILED = "doc_write_failed"
REASON_STORE_WRITE_FAILED = "store_write_failed"
REASON_EVENT_HANDLER_FAILED = "event_handler_failed"
REASON_EXTRACT_FAILED = "extract_failed"
REASON_MAIL_DELIVERY_FAILED = "mail_delivery_failed"
REASON_VERIFIER_UNAVAILABLE = "verifier_unavailable"
REASON_CASE_FAILED = "case_failed"
REASON_UNEXPECTED = "unexpected"

logger = get_logger("degradation")


def degraded_channel(
    channel: RetrievalChannel | str,
    reason: str,
    status: str = ChannelStatus.DEGRADED,
) -> dict[str, str]:
    """One ``degraded_channels`` entry — the shape the API envelope and SSE use."""
    return {"channel": str(channel), "reason": reason, "status": status}


def merge_degraded_channels(*groups: Any) -> list[dict[str, Any]]:
    """Union of ``degraded_channels`` lists by channel name; later groups win.

    Layers that cannot see the run's :class:`ChannelHealth` (the extractive answer
    fallback, for instance) prepend their own entry; this keeps one entry per
    channel and a stable order in the response.
    """
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for row in group if isinstance(group, list) else []:
            if isinstance(row, dict) and row.get("channel"):
                merged[str(row["channel"])] = dict(row)
    return [merged[key] for key in sorted(merged)]


@dataclass
class ChannelHealth:
    """Per-run health of the retrieval channels, shared across worker threads."""

    _status: dict[str, str] = field(default_factory=dict)
    _reason: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def mark(self, channel: str, status: str, reason: str = "") -> None:
        """Record *status* for *channel*, keeping the worst status and its reason."""
        with self._lock:
            current = self._status.get(channel)
            if current is not None and _SEVERITY.get(status, 1) <= _SEVERITY.get(current, 0):
                return
            self._status[channel] = status
            self._reason[channel] = reason

    def failed_channels(self) -> set[str]:
        """Channels that produced no usable evidence at all."""
        with self._lock:
            return {name for name, status in self._status.items() if status == ChannelStatus.FAILED}

    def degraded_channels(self) -> list[dict[str, str]]:
        """Every channel that is not fully healthy, sorted by channel name."""
        with self._lock:
            rows = [
                degraded_channel(name, self._reason.get(name, ""), status)
                for name, status in sorted(self._status.items())
                if status != ChannelStatus.OK
            ]
        return rows


def record_degradation(
    component: RetrievalChannel | str,
    reason: str,
    exc: BaseException | None = None,
    *,
    severity: str = ChannelStatus.DEGRADED,
    health: ChannelHealth | None = None,
    **attrs: Any,
) -> None:
    """Log, count, trace and (when tracked) mark one degraded dependency.

    Called from inside ``except`` blocks: it must never raise, and it must never
    make the caller's failure worse. *component* is the failing dependency —
    a :class:`RetrievalChannel` value or a subsystem name. *severity* is
    ``degraded`` when a fallback covered the loss and ``failed`` when the channel
    returned nothing.
    """
    mark = str(severity) if str(severity) in _SEVERITY else ChannelStatus.DEGRADED
    name = str(component)
    error_type = type(exc).__name__ if exc is not None else ""
    tenant_id = tenant_id_var.get()
    _log(component=name, reason=reason, severity=mark, error_type=error_type, attrs=attrs)
    _count(name, reason, tenant_id)
    otel_span_event(
        "degradation",
        attributes={
            "agr.component": name,
            "agr.reason": reason,
            "agr.severity": mark,
            "agr.error_type": error_type,
        },
    )
    if health is not None and name in CHANNELS:
        health.mark(name, mark, reason)


def _log(
    *,
    component: str,
    reason: str,
    severity: str,
    error_type: str,
    attrs: dict[str, Any],
) -> None:
    try:
        logger.warning(
            "degraded component=%s reason=%s severity=%s",
            component,
            reason,
            severity,
            extra={
                "component": component,
                "reason": reason,
                "severity": severity,
                "error_type": error_type,
                "tenant_id": tenant_id_var.get(),
                "query_id": query_id_var.get(),
                **attrs,
            },
        )
    except Exception:  # noqa: BLE001 — logging must not break the caller
        return


def _count(component: str, reason: str, tenant_id: str) -> None:
    try:
        get_metrics().record_degradation(component=component, reason=reason, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001 — metrics must not break the caller
        return


@contextlib.contextmanager
def degradation_guard(
    component: RetrievalChannel | str,
    reason: str,
    *,
    severity: str = ChannelStatus.DEGRADED,
    health: ChannelHealth | None = None,
    **attrs: Any,
) -> Iterator[None]:
    """Run a block; on failure record the degradation and re-raise it.

    The compact form of "keep the fallback, make it visible": the caller keeps its
    own ``raise``/fallback logic and pays two lines for the record.
    """
    try:
        yield
    except Exception as exc:
        record_degradation(component, reason, exc, severity=severity, health=health, **attrs)
        raise
