"""Issued model usage survives cancellation; subsequent calls never start."""

import threading

import pytest

from agentic_graphrag.app import query_agent, query_run, query_stream
from agentic_graphrag.app.contracts import QueryRequest
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.config import AppConfig, Settings
from agentic_graphrag.core.ports.llm import Message
from agentic_graphrag.llm.provider import LLMProvider


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_DIR", str(tmp_path / "runtime"))
    cfg = AppConfig()
    cfg.paths.cache_dir = str(tmp_path / "cache")
    svc = QueryService.create_offline(cfg=cfg, settings=Settings(), seed_triples=tmp_path / "empty")
    svc.allow_llm = True
    svc.enable_cache = False
    yield svc
    svc.close()


def response():
    return {
        "choices": [{"message": {"content": "result"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 17, "completion_tokens": 5},
    }


def test_disconnect_settles_actual_usage(service, monkeypatch):
    calls = []

    def build(**kwargs):
        client = LLMProvider(api_key="test", budget=kwargs["budget"])
        client._post = lambda *a, **k: calls.append("issued") or response()
        return client

    def progress(question, executor, llm, *, options):
        llm.complete([Message(role="user", content="first")])
        yield "thinking", {"message": "one call completed"}
        llm.complete([Message(role="user", content="must not run")])

    monkeypatch.setattr(query_stream, "build_llm_for_service", build)
    monkeypatch.setattr(query_stream, "iter_query_progress", progress)
    stream = service.stream_query_events(QueryRequest(question="q", force_agentic=True))
    assert next(stream)[0] == "thinking"
    stream.close()
    usage = service.multi_budget.snapshot()["tenants"]["default"]
    assert calls == ["issued"]
    assert (usage["llm_calls"], usage["tokens"]) == (1, 22)


def test_timeout_releases_reservation_then_accounts_late_response(service, monkeypatch):
    release, finished = threading.Event(), threading.Event()
    calls = []

    def post(*args, **kwargs):
        calls.append("issued")
        assert release.wait(5)
        return response()

    def build(**kwargs):
        client = LLMProvider(api_key="test", budget=kwargs["budget"])
        client._post = post
        client.close = finished.set
        return client

    def agent(question, executor, llm, *, options):
        llm.complete([Message(role="user", content="first")])
        llm.complete([Message(role="user", content="must not run")])
        raise AssertionError("Cancelled query continued")

    monkeypatch.setattr(query_run, "build_llm_for_service", build)
    monkeypatch.setattr(query_agent, "agent_run_query", agent)
    try:
        with pytest.raises(ApiError, match="timed out"):
            service.run_query(QueryRequest(question="q", force_agentic=True, timeout_ms=100))
        assert service.multi_budget.snapshot()["tenants"]["default"]["llm_calls"] == 0
    finally:
        release.set()
    assert finished.wait(5)
    assert calls == ["issued"]
    usage = service.multi_budget.snapshot()["tenants"]["default"]
    assert (usage["llm_calls"], usage["tokens"]) == (1, 22)
