"""Reliable delivery for critical event subscribers (ARCH_OPTIMIZATION_V2 O-4)."""

from __future__ import annotations

from types import SimpleNamespace

from agentic_graphrag.app.write_path import drop_caches, subscribe_read_side
from agentic_graphrag.core.events import GraphCommitted, InProcessEventBus, RecordingEventBus
from agentic_graphrag.observability.metrics import get_metrics
from agentic_graphrag.retrieval.cache import RetrievalCache


class _Flaky:
    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def __call__(self, _event) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("boom")


def test_critical_handler_is_retried_until_success() -> None:
    bus = InProcessEventBus()
    flaky = _Flaky(fail_times=2)
    bus.subscribe(GraphCommitted, flaky, critical=True, retries=2)
    bus.publish(GraphCommitted(tenant_id="t"))
    assert flaky.calls == 3
    assert not bus.handler_failures


def test_exhausted_retries_run_fail_safe_and_count() -> None:
    bus = InProcessEventBus()
    seen: list[str] = []
    bus.subscribe(
        GraphCommitted,
        _Flaky(fail_times=99),
        critical=True,
        retries=1,
        on_failure=lambda ev, exc: seen.append(f"{ev.name}:{exc}"),
    )
    bus.publish(GraphCommitted())
    assert seen == ["GraphCommitted:boom"]
    assert sum(bus.handler_failures.values()) == 1


def test_non_critical_is_not_retried_and_fail_safe_errors_are_contained() -> None:
    bus = InProcessEventBus()
    plain = _Flaky(fail_times=5)
    bus.subscribe(GraphCommitted, plain, retries=3)  # retries ignored when not critical

    def bad_fail_safe(_ev, _exc):
        raise ValueError("fail-safe broke")

    bus.subscribe(GraphCommitted, _Flaky(9), critical=True, on_failure=bad_fail_safe)
    after: list[int] = []
    bus.subscribe(GraphCommitted, lambda _e: after.append(1))
    bus.publish(GraphCommitted())
    assert plain.calls == 1
    assert after == [1]  # later subscribers still run


def _svc(cache) -> SimpleNamespace:
    graph = SimpleNamespace(list_entities=lambda limit: [])
    return SimpleNamespace(
        retrieval_cache=cache, bundle=SimpleNamespace(graph=graph), known_entities=[]
    )


def test_failed_invalidation_drops_caches() -> None:
    cache = RetrievalCache()
    cache.answers.set("k", "stale answer")
    cache.retrieval.set("r", ["stale"])

    def broken_bump() -> int:
        raise RuntimeError("version store down")

    cache.on_index_update = broken_bump  # type: ignore[method-assign]
    bus = RecordingEventBus()
    before = sum(get_metrics().summary()["event_handler_failures"].values())
    subscribe_read_side(_svc(cache), bus)
    bus.publish(GraphCommitted(tenant_id="t"))
    assert cache.answers.get("k") is None and cache.retrieval.get("r") is None
    after = sum(get_metrics().summary()["event_handler_failures"].values())
    assert after == before + 1


def test_drop_caches_without_cache_is_noop() -> None:
    drop_caches(_svc(None), GraphCommitted(), RuntimeError("x"))


def test_prometheus_exports_event_handler_failures() -> None:
    from agentic_graphrag.api.routes.admin import prometheus_metrics_text

    get_metrics().record_event_failure("GraphCommitted:invalidate:RuntimeError")
    text = prometheus_metrics_text()
    assert "# TYPE agr_event_handler_failures_total counter" in text
    assert 'handler="GraphCommitted:invalidate:RuntimeError"' in text
