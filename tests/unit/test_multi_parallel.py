"""Real reasoning fan-out, deduplication and branch observability contracts."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event, Lock

import pytest
from test_multi_runtime import ResearchLLM, setup_run

from agentic_graphrag.agent.loop import invoke_config
from agentic_graphrag.agent.loop_stream_events import EVENT_FINAL_CHAIN
from agentic_graphrag.agent.multi_graph import build_multi_graph
from agentic_graphrag.agent.options import QueryOptions
from agentic_graphrag.agent.pipeline import iter_query_progress
from agentic_graphrag.agent.pipeline_graph import _stream_graph_updates
from agentic_graphrag.agent.pipeline_multi import checkpoint_thread
from agentic_graphrag.agent.research_cache import QueryDedup, scoped_key
from agentic_graphrag.config_agent import AgentConfig
from agentic_graphrag.governance.execution import ExecutionStopped
from agentic_graphrag.observability.branch import branch_span
from agentic_graphrag.observability.logging_setup import tenant_id_var


def test_pipeline_streams_multi_branches_and_final_answer(monkeypatch):
    deps, config, _, _ = setup_run(monkeypatch)
    events = list(
        iter_query_progress(
            "Who leads Acme?",
            deps.executor,
            deps.llm,
            options=QueryOptions(
                force_agentic=True,
                allow_llm=True,
                tenant_id="tenant",
                query_id="pipeline",
                agent_config=config,
                guard_cfg=deps.guard_cfg,
            ),
        )
    )
    assert any(kind == "sub_question" and value["branch_id"] for kind, value in events)
    chain = next(value for kind, value in events if kind == EVENT_FINAL_CHAIN)
    assert chain.metadata["agent_mode"] == "multi"
    assert chain.claims[0].evidence_ids == ["c1"]


def test_three_researchers_reason_concurrently_with_local_state(monkeypatch):
    class ParallelLLM(ResearchLLM):
        def __init__(self):
            super().__init__({rid: {"text": f"Who leads Acme {rid}?"} for rid in "abc"})
            self.barrier = Barrier(3, timeout=5)
            self.identities = []

        def complete(self, messages, **kwargs):
            if "Research exactly" in messages[-1].content:
                data = json.loads(messages[-1].content.split("Input:\n", 1)[1])
                assert "register" not in data and "context" not in data
                self.identities.append(tenant_id_var.get())
                self.barrier.wait()
            return super().complete(messages, **kwargs)

    llm = ParallelLLM()
    deps, config, state, calls = setup_run(
        monkeypatch, llm=llm, config=AgentConfig(mode="multi", max_parallel=3)
    )
    spans = []

    @contextmanager
    def capture(**attributes):
        row = dict(attributes)
        spans.append(row)
        yield row

    monkeypatch.setattr("agentic_graphrag.agent.multi_graph.branch_span", capture)
    graph = build_multi_graph(deps, config)
    events = list(
        _stream_graph_updates(graph, state, invoke_config("parallel", recursion_limit=40))
    )
    assert sorted(llm.research_calls) == ["a", "b", "c"]
    assert llm.identities == ["tenant"] * 3 and calls == ["tenant"] * 3
    assert len(llm.verifier_inputs) == 1 and len(llm.verifier_inputs[0]) == 3
    starts = [p for kind, p in events if kind == "sub_question"]
    assert len({p["branch_id"] for p in starts}) == 3
    assert all(p["branch_count"] == 3 for p in starts)
    assert len(spans) == 3 and all(s["tokens"] == 15 and s["outcome"] == "resolved" for s in spans)


def test_dedup_is_single_flight_and_scoped_to_tenant_and_run():
    cache = QueryDedup()
    key = scoped_key("tenant", "run", tool="fulltext_search", args={"query": "A B"})
    started, release = Event(), Event()
    count = []

    def retrieve():
        count.append(1)
        started.set()
        assert release.wait(5)
        return []

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(cache.retrieve, key, retrieve)
        assert started.wait(5)
        second = pool.submit(cache.retrieve, key, retrieve)
        release.set()
        assert first.result() == second.result() == []
    assert len(count) == 1
    assert cache.retrieve(key, lambda: pytest.fail("empty result was not cached")) == []
    other = scoped_key("other", "run", tool="fulltext_search", args={"query": "A B"})
    next_run = scoped_key("tenant", "next", tool="fulltext_search", args={"query": "A B"})
    assert len({key, other, next_run}) == 3
    cache.retrieve(other, retrieve)
    cache.retrieve(next_run, retrieve)
    assert len(count) == 3
    assert checkpoint_thread("a", "r") != checkpoint_thread("b", "r")


def test_failed_queries_are_not_cached():
    cache = QueryDedup()
    with pytest.raises(RuntimeError):
        cache.retrieve("key", lambda: (_ for _ in ()).throw(RuntimeError("failed")))
    assert cache.retrieve("key", lambda: []) == []


@pytest.mark.parametrize("reason", ["Query cancelled", "Query timed out"])
def test_cancelled_single_flight_wakes_waiters_and_allows_retry(monkeypatch, reason):
    from concurrent.futures import Future

    waiting, started, release = Event(), Event(), Event()

    class ObservedFuture(Future):
        def result(self, *args, **kwargs):
            waiting.set()
            return super().result(*args, **kwargs)

    monkeypatch.setattr("agentic_graphrag.agent.research_cache.Future", ObservedFuture)
    cache = QueryDedup()

    def stop():
        started.set()
        assert release.wait(5)
        raise ExecutionStopped(reason)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.retrieve, "key", stop)
        assert started.wait(5)
        pending = cache._pending["key"]
        second = pool.submit(cache.retrieve, "key", lambda: pytest.fail("duplicate retrieval"))
        try:
            assert waiting.wait(5)
            release.set()
            for future in (first, second):
                with pytest.raises(ExecutionStopped, match=reason):
                    future.result(timeout=1)
            assert cache.retrieve("key", lambda: []) == []
        finally:
            release.set()
            if not pending.done():
                pending.set_exception(ExecutionStopped(reason))


def test_branch_span_publishes_final_attributes_and_preserves_errors(monkeypatch):
    captured = {}

    class Span:
        def set_attributes(self, attrs):
            captured.update(attrs)

    @contextmanager
    def span(*_args, **_kwargs):
        yield Span()

    monkeypatch.setattr("agentic_graphrag.observability.branch.otel_span", span)
    with pytest.raises(RuntimeError, match="branch failed"):
        with branch_span(req_id="a", role="researcher", model="test") as attrs:
            attrs["tokens"] = 42
            raise RuntimeError("branch failed")
    assert captured == {
        "req_id": "a",
        "role": "researcher",
        "model": "test",
        "tokens": 42,
        "outcome": "error",
    }


def test_concurrency_one_remains_serial(monkeypatch):
    class SerialLLM(ResearchLLM):
        def __init__(self):
            super().__init__({rid: {"text": f"Who leads Acme {rid}?"} for rid in "abc"})
            self.lock = Lock()

        def complete(self, messages, **kwargs):
            assert self.lock.acquire(blocking=False)
            try:
                return super().complete(messages, **kwargs)
            finally:
                self.lock.release()

    deps, config, state, _ = setup_run(monkeypatch, llm=SerialLLM())
    result = build_multi_graph(deps, config).invoke(
        state, invoke_config("serial", recursion_limit=40)
    )
    assert result["register"].is_clear()
