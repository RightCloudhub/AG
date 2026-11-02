"""Query identity follows workers and remains isolated between query streams."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from agentic_graphrag.agent.executor import Executor, ToolCallSpec
from agentic_graphrag.agent.parallel_branches import BranchRun, retrieve_group
from agentic_graphrag.app.contracts import QueryRequest
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.config import get_config
from agentic_graphrag.core.enums import RetrievalTool, StreamEvent
from agentic_graphrag.observability import metrics
from agentic_graphrag.observability.degradation import record_degradation
from agentic_graphrag.observability.logging_setup import (
    bind_context,
    clear_context,
    query_id_var,
    tenant_id_var,
)
from agentic_graphrag.observability.trace import get_tracer
from agentic_graphrag.stores.vector_store import InMemoryVectorStore


@pytest.fixture
def isolated_metrics(monkeypatch):
    registry = metrics.MetricsRegistry()
    monkeypatch.setattr(metrics, "_GLOBAL", registry)
    bind_context(tenant_id="parent", query_id="parent-query", request_id="request")
    try:
        yield registry
    finally:
        clear_context()


@contextmanager
def warnings():
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logger = logging.getLogger("agentic_graphrag")
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


@pytest.fixture
def service(monkeypatch):
    cfg = get_config().model_copy(deep=True)
    cfg.retrieval.parallel = True
    svc = QueryService.create_offline(cfg=cfg)
    specs = [
        ToolCallSpec(tool=tool)
        for tool in (
            RetrievalTool.VECTOR_SEARCH,
            RetrievalTool.FULLTEXT_SEARCH,
        )
    ]
    monkeypatch.setattr(Executor, "_choose_tools", Mock(return_value=specs))
    monkeypatch.setattr(
        InMemoryVectorStore, "search", Mock(side_effect=ConnectionError("store unavailable"))
    )
    try:
        yield svc
    finally:
        svc.close()


@pytest.mark.parametrize("stream", [False, True])
def test_worker_warning_and_metrics_use_public_query_identity(service, isolated_metrics, stream):
    request = QueryRequest(
        question="Find the requested information.", force_agentic=True, max_hops=1
    )
    query_ids = []
    for tenant in ("alpha", "beta"):
        with warnings() as records:
            if stream:
                events = dict(service.stream_query_events(request, tenant_id=tenant, user_id="u"))
                query_id = events[StreamEvent.ANSWER]["query_id"]
            else:
                query_id = service.run_query(request, tenant_id=tenant, user_id="u").query_id
        relevant = [r for r in records if getattr(r, "component", "") == "vector"]
        assert relevant
        assert all(r.tenant_id == tenant and r.query_id == query_id for r in relevant)
        assert isolated_metrics.summary(tenant_id=tenant)["degradations"]
        assert get_tracer().get(query_id) is not None
        assert (tenant_id_var.get(), query_id_var.get()) == ("parent", "parent-query")
        query_ids.append(query_id)
    assert query_ids[0] != query_ids[1]
    assert isolated_metrics.summary(tenant_id="default")["degradations"] == []


def test_each_submission_gets_its_own_context(isolated_metrics):
    from agentic_graphrag.observability.context import submit_with_context

    barrier = threading.Barrier(2)

    def worker(value):
        initial = (tenant_id_var.get(), query_id_var.get())
        bind_context(tenant_id=value)
        barrier.wait(timeout=5)
        return initial, tenant_id_var.get()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [submit_with_context(pool, worker, value) for value in ("alpha", "beta")]
        assert [f.result() for f in futures] == [
            (("parent", "parent-query"), "alpha"),
            (("parent", "parent-query"), "beta"),
        ]
    assert tenant_id_var.get() == "parent"


def test_parallel_dag_branches_keep_context(isolated_metrics):
    barrier = threading.Barrier(2)

    class FakeExecutor:
        def run(self, text, **kwargs):
            barrier.wait(timeout=5)
            record_degradation("vector", "vector_store_unavailable", ConnectionError())
            return [], []

    with warnings() as records:
        retrieve_group(
            FakeExecutor(),
            [("a", "first"), ("b", "second")],
            BranchRun(hints_for=lambda text: [], allow_llm=False, tenant_id="parent"),
        )
    assert len(records) == 2
    assert all(r.tenant_id == "parent" and r.query_id == "parent-query" for r in records)
    assert isolated_metrics.summary(tenant_id="parent")["degradations"][0]["count"] == 2


def test_interleaved_streams_restore_the_callers_context(service, isolated_metrics):
    request = QueryRequest(question="Find information.", force_agentic=True, max_hops=1)
    streams = {
        tenant: service.stream_query_events(request, tenant_id=tenant, user_id="u")
        for tenant in ("alpha", "beta")
    }
    answers = {}
    with warnings() as records:
        while streams:
            for tenant, stream in list(streams.items()):
                event = next(stream, None)
                if event is None:
                    del streams[tenant]
                elif event[0] == StreamEvent.ANSWER:
                    answers[tenant] = event[1]["query_id"]
                assert (tenant_id_var.get(), query_id_var.get()) == ("parent", "parent-query")
    relevant = [r for r in records if getattr(r, "component", "") == "vector"]
    assert relevant
    assert all(r.tenant_id in answers and r.query_id == answers[r.tenant_id] for r in relevant)


def test_closing_a_stream_releases_budget_in_its_own_context(
    service, isolated_metrics, monkeypatch
):
    from agentic_graphrag.app import query_stream

    released = []

    def release(*args, **kwargs):
        released.append((tenant_id_var.get(), query_id_var.get()))

    monkeypatch.setattr(query_stream, "_release_budget", release)
    stream = service.stream_query_events(
        QueryRequest(question="Find information.", force_agentic=True),
        tenant_id="alpha",
        user_id="u",
    )
    assert next(stream)[0] == StreamEvent.TRIAGE
    assert tenant_id_var.get() == "parent"
    stream.close()
    assert len(released) == 1
    assert released[0][0] == "alpha"
    assert released[0][1] and released[0][1] != "parent-query"
    assert (tenant_id_var.get(), query_id_var.get()) == ("parent", "parent-query")


@pytest.mark.parametrize("component", ["audit_store", "answer_cache"])
def test_post_query_failures_keep_the_query_identity(
    service, isolated_metrics, monkeypatch, component
):
    target, method = (
        (service.audit_store, "save")
        if component == "audit_store"
        else (service.retrieval_cache, "set_answer")
    )
    monkeypatch.setattr(target, method, Mock(side_effect=OSError("write failed")))
    with warnings() as records:
        result = service.run_query(QueryRequest(question="hello"), tenant_id="alpha", user_id="u")
    relevant = [r for r in records if getattr(r, "component", "") == component]
    assert len(relevant) == 1
    assert (relevant[0].tenant_id, relevant[0].query_id) == ("alpha", result.query_id)
    assert isolated_metrics.summary(tenant_id="alpha")["degradations"][0]["component"] == component
    assert (tenant_id_var.get(), query_id_var.get()) == ("parent", "parent-query")
