"""Optional tracing must preserve application results and exceptions."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from agentic_graphrag.observability import otel_bridge


@pytest.mark.parametrize("failure", ["construct", "enter", "exit", None])
@pytest.mark.parametrize("business_error", [False, True])
def test_tracing_failures_do_not_replace_application_outcome(monkeypatch, failure, business_error):
    closed = []

    @contextmanager
    def span_context():
        if failure == "enter":
            raise RuntimeError("span processor failed to start")
        try:
            yield "span"
        finally:
            closed.append(True)
            if failure == "exit":
                raise RuntimeError("span processor failed to end")

    def start(*_args, **_kwargs):
        if failure == "construct":
            raise RuntimeError("tracer unavailable")
        return span_context()

    monkeypatch.setattr(otel_bridge, "_ENABLED", True)
    monkeypatch.setattr(otel_bridge, "_TRACER", SimpleNamespace(start_as_current_span=start))
    expected_error = ValueError("business failure")
    ran = []

    def operation():
        with otel_bridge.otel_span("query") as span:
            ran.append(True)
            assert span == (None if failure in {"construct", "enter"} else "span")
            if business_error:
                raise expected_error
            return "answer"

    if business_error:
        with pytest.raises(ValueError) as caught:
            operation()
        assert caught.value is expected_error
    else:
        assert operation() == "answer"
    assert ran == [True]
    assert closed == ([] if failure in {"construct", "enter"} else [True])
