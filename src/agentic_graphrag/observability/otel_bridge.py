"""Optional OpenTelemetry bridge — OTLP span export + W3C trace context (ENT-08).

Gracefully no-ops when ``opentelemetry-sdk`` is not installed.

Usage::

    from agentic_graphrag.observability.otel_bridge import setup_otel, otel_span
    setup_otel(service_name="agentic-graphrag", endpoint="http://otel-collector:4317")

    with otel_span("my_operation", attributes={"query": "..."}) as span:
        ...
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Generator
from typing import Any

logger = logging.getLogger(__name__)

_ENABLED = False
_TRACER: Any = None


def setup_otel(
    *,
    service_name: str = "agentic-graphrag",
    endpoint: str | None = None,
    sample_rate: float = 1.0,
) -> bool:
    """Initialize TracerProvider + OTLP exporter. Returns True if enabled."""
    global _ENABLED, _TRACER  # noqa: PLW0603
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    except ImportError:
        logger.debug("opentelemetry-sdk not installed; OTel bridge disabled")
        return False

    resource = Resource.create({"service.name": service_name})
    sampler = TraceIdRatioBased(sample_rate)
    provider = TracerProvider(resource=resource, sampler=sampler)

    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except ImportError:
            logger.debug("OTLP exporter not installed; spans will not be exported")

    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer(service_name)
    _ENABLED = True
    logger.info("OTel bridge enabled: service=%s endpoint=%s", service_name, endpoint)
    return True


@contextlib.contextmanager
def otel_span(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
) -> Generator[Any, None, None]:
    """Context manager: creates an OTel span when enabled, no-op otherwise."""
    if not _ENABLED or _TRACER is None:
        yield None
        return
    try:
        with _TRACER.start_as_current_span(name, attributes=attributes or {}) as span:
            yield span
    except Exception:  # noqa: BLE001 — never break application on OTel failure
        yield None


def extract_trace_context(headers: dict[str, str]) -> dict[str, str] | None:
    """Parse W3C traceparent/tracestate from request headers."""
    if not _ENABLED:
        return None
    try:
        from opentelemetry import propagate

        carrier = {k.lower(): v for k, v in headers.items()}
        ctx = propagate.extract(carrier)
        if ctx:
            return {"traceparent": carrier.get("traceparent", "")}
    except Exception:  # noqa: BLE001
        pass
    return None


@contextlib.contextmanager
def extract_otel_context(headers: dict[str, str]) -> Generator[Any, None, None]:
    """Attach inbound W3C trace context for the duration of a request."""
    if not _ENABLED:
        yield None
        return
    try:
        from opentelemetry import context, propagate

        extracted = propagate.extract({k.lower(): v for k, v in headers.items()})
        token = context.attach(extracted)
        try:
            yield extracted
        finally:
            context.detach(token)
    except Exception:  # noqa: BLE001
        yield None


def inject_trace_context(headers: dict[str, str]) -> dict[str, str]:
    """Inject W3C traceparent into outgoing headers."""
    if not _ENABLED:
        return headers
    try:
        from opentelemetry import propagate

        out = dict(headers)
        propagate.inject(out)
        return out
    except Exception:  # noqa: BLE001
        return headers
