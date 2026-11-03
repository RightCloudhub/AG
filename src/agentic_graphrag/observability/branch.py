"""One OTel span per researcher invocation, including failures and token usage."""

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

from agentic_graphrag.observability.otel_bridge import otel_span


@contextmanager
def branch_span(**attributes: str | int) -> Iterator[dict[str, str | int]]:
    values: dict[str, str | int] = {"tokens": 0, "outcome": "error", **attributes}
    with otel_span("agent.researcher", attributes=values) as span:
        try:
            yield values
        finally:
            _publish(span, values)


def _publish(span: Any, values: dict[str, str | int]) -> None:
    # Optional telemetry must never replace an application exception.
    if span is not None:
        with suppress(Exception):
            span.set_attributes(values)
