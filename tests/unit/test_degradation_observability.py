"""O-9: a degraded run is logged, counted and visible to the caller.

Everything here is offline: a fake embedder, a vector store and a graph store
that raise, and the in-memory profile the rest of the unit suite uses.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agentic_graphrag.agent.executor import ToolCallSpec
from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.routes.admin import prometheus_metrics_text
from agentic_graphrag.api.schemas import QueryRequest
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.app.degradation_policy import apply_degradation_policy
from agentic_graphrag.config import AppConfig, get_config, get_settings
from agentic_graphrag.core.enums import RetrievalChannel, RetrievalTool
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.llm.provider import MockLLMProvider
from agentic_graphrag.observability.degradation import (
    ChannelHealth,
    ChannelStatus,
    degraded_channel,
    record_degradation,
)
from agentic_graphrag.observability.metrics import get_metrics
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore

QUESTION = "Who is the CEO of the parent company of NovaTech Industries?"
SEED_TRIPLES = "data/processed/seed_triples.jsonl"


class _Embedder401(MockLLMProvider):
    """Fails the way an expired or wrong gateway key does."""

    def embed(self, text: str) -> list[float]:
        request = httpx.Request("POST", "https://llm.invalid/v1/embeddings")
        response = httpx.Response(401, request=request, json={"error": "invalid api key"})
        raise httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)


def _store_down(*_args: Any, **_kwargs: Any) -> None:
    raise ConnectionError("store unreachable")


@contextmanager
def _force_tools(monkeypatch: pytest.MonkeyPatch, tools: list[RetrievalTool]) -> Iterator[None]:
    """Pin tool selection so the channel under test is always exercised."""
    from agentic_graphrag.agent import executor as executor_mod

    def fake(
        executor: Any, sub_question: str, entities_hint: list[str], *, allow_llm: bool
    ) -> list[ToolCallSpec]:
        del executor, sub_question, entities_hint, allow_llm
        return [ToolCallSpec(tool=tool) for tool in tools]

    monkeypatch.setattr(executor_mod, "choose_tools", fake)
    yield


class _Capture(logging.Handler):
    """Collects the warning lines the degradation logger emits."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(str(record.getMessage()))


@contextmanager
def _degradation_warnings() -> Iterator[_Capture]:
    # Attached to the application logger itself: ``setup_logging`` sets
    # propagate=False, so a root-level caplog handler would miss these records.
    handler = _Capture()
    target = logging.getLogger("agentic_graphrag")
    target.addHandler(handler)
    try:
        yield handler
    finally:
        target.removeHandler(handler)


def _offline_service(cfg: AppConfig | None = None) -> QueryService:
    resolved = cfg or get_config().model_copy(deep=True)
    return QueryService.create_offline(
        seed_triples=SEED_TRIPLES, cfg=resolved, settings=get_settings()
    )


def _config_with(low_confidence_channels: list[str]) -> AppConfig:
    cfg = get_config().model_copy(deep=True)
    cfg.degradation.low_confidence_channels = low_confidence_channels  # type: ignore[assignment]
    return cfg


def _counter(component: str, reason: str) -> int:
    for row in get_metrics().summary()["degradations"]:
        if row["component"] == component and row["reason"] == reason:
            return int(row["count"])
    return 0


def _post_query(svc: QueryService) -> dict[str, Any]:
    app = create_app(query_service=svc)
    with TestClient(app) as client:
        response = client.post("/v1/query", json={"question": QUESTION, "force_agentic": True})
    assert response.status_code == 200, response.text
    return response.json()


def _channels(body: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {row["channel"]: row for row in body["data"]["degraded_channels"]}


def test_healthy_run_reports_no_degradation(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean run must not claim a degraded channel."""
    svc = _offline_service()
    try:
        with _force_tools(
            monkeypatch, [RetrievalTool.VECTOR_SEARCH, RetrievalTool.GRAPH_NEIGHBORS]
        ):
            body = _post_query(svc)
        assert body["data"]["degraded_channels"] == []
        assert body["data"]["low_confidence"] is False
        assert body["meta"]["degraded_channels"] == []
    finally:
        svc.close()


def test_fake_embedder_401_is_reported_as_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 from the embedder shows up as the embedding channel, logged and counted."""
    from agentic_graphrag.app import wiring

    svc = _offline_service()
    monkeypatch.setattr(wiring, "_embed_llm", lambda **_kwargs: _Embedder401())
    before = _counter("embedding", "embedding_unavailable")
    try:
        with (
            _force_tools(monkeypatch, [RetrievalTool.VECTOR_SEARCH]),
            _degradation_warnings() as warnings,
        ):
            body = _post_query(svc)
    finally:
        svc.close()

    channels = _channels(body)
    assert channels["embedding"]["status"] == ChannelStatus.FAILED
    assert channels["embedding"]["reason"] == "embedding_unavailable"
    # No embedding means no vector search either, and the reason says so.
    assert channels["vector"]["status"] == ChannelStatus.FAILED
    assert channels["vector"]["reason"] == "embedding_unavailable"
    assert body["meta"]["degraded_channels"] == body["data"]["degraded_channels"]
    assert _counter("embedding", "embedding_unavailable") > before
    assert any("component=embedding" in message for message in warnings.messages)


def test_vector_store_down_is_reported_as_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _offline_service()
    monkeypatch.setattr(InMemoryVectorStore, "search", _store_down)
    before = _counter("vector", "vector_store_unavailable")
    try:
        with (
            _force_tools(monkeypatch, [RetrievalTool.VECTOR_SEARCH]),
            _degradation_warnings() as warnings,
        ):
            body = _post_query(svc)
    finally:
        svc.close()

    channels = _channels(body)
    assert channels["vector"]["reason"] == "vector_store_unavailable"
    assert channels["vector"]["status"] == ChannelStatus.FAILED
    assert "embedding" not in channels
    assert _counter("vector", "vector_store_unavailable") > before
    assert any("component=vector" in message for message in warnings.messages)


def test_vector_and_graph_down_mark_the_answer_low_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both channels gone: the answer must not pass as a normal one."""
    svc = _offline_service()
    monkeypatch.setattr(InMemoryVectorStore, "search", _store_down)
    monkeypatch.setattr(InMemoryGraphStore, "neighbors", _store_down)
    try:
        with _force_tools(
            monkeypatch, [RetrievalTool.VECTOR_SEARCH, RetrievalTool.GRAPH_NEIGHBORS]
        ):
            body = _post_query(svc)
    finally:
        svc.close()

    channels = _channels(body)
    assert channels["vector"]["status"] == ChannelStatus.FAILED
    assert channels["graph"]["status"] == ChannelStatus.FAILED
    assert body["data"]["low_confidence"] is True
    assert body["data"]["metadata"]["low_confidence"] is True


def test_low_confidence_threshold_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    """``degradation.low_confidence_channels`` decides when the flag is set."""
    svc = _offline_service(_config_with(["vector"]))
    monkeypatch.setattr(InMemoryVectorStore, "search", _store_down)
    try:
        with _force_tools(monkeypatch, [RetrievalTool.VECTOR_SEARCH]):
            body = _post_query(svc)
    finally:
        svc.close()
    assert body["data"]["low_confidence"] is True


def test_stream_emits_degraded_before_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _offline_service()
    monkeypatch.setattr(InMemoryVectorStore, "search", _store_down)
    try:
        with _force_tools(monkeypatch, [RetrievalTool.VECTOR_SEARCH]):
            events = list(
                svc.stream_query_events(
                    QueryRequest(question=QUESTION, force_agentic=True),
                    tenant_id="default",
                    user_id="u",
                )
            )
    finally:
        svc.close()

    names = [name for name, _ in events]
    assert "degraded" in names
    payload = dict(events)["degraded"]
    assert payload["low_confidence"] is False  # only one of the two channels failed
    assert [row["channel"] for row in payload["channels"]] == ["vector"]
    assert names.index("degraded") < names.index("answer")


def test_chain_survives_the_trip_through_the_response() -> None:
    """Promotion from chain metadata to response fields (app/wiring.py)."""
    from agentic_graphrag.app.wiring import _chain_to_data

    chain = ReasoningChain(question="q")
    health = ChannelHealth()
    record_degradation(
        RetrievalChannel.GRAPH,
        "graph_store_unavailable",
        None,
        severity=ChannelStatus.FAILED,
        health=health,
    )
    apply_degradation_policy(chain, health, _config_with([RetrievalChannel.GRAPH]))
    data = _chain_to_data(chain)
    assert [row.channel for row in data.degraded_channels] == ["graph"]
    assert data.low_confidence is True
    # The flag follows the configured set: with the default {vector, graph} a
    # graph-only failure is reported but is not treated as low confidence.
    other = ReasoningChain(question="q")
    apply_degradation_policy(
        other, health, _config_with([RetrievalChannel.VECTOR, RetrievalChannel.GRAPH])
    )
    assert "low_confidence" not in other.metadata
    assert [row["channel"] for row in other.metadata["degraded_channels"]] == ["graph"]


def test_llm_fallback_marks_the_llm_channel() -> None:
    """The extractive fallback is recorded below the executor, via chain metadata."""
    chain = ReasoningChain(question="q", metadata={"llm_degraded": True})
    apply_degradation_policy(chain, ChannelHealth(), _config_with(["vector", "graph"]))
    rows = chain.metadata["degraded_channels"]
    assert [row["channel"] for row in rows] == ["llm"]
    assert "low_confidence" not in chain.metadata


def test_record_degradation_marks_health_and_counts() -> None:
    health = ChannelHealth()
    with _degradation_warnings() as warnings:
        record_degradation(
            RetrievalChannel.RERANK, "rerank_unavailable", ConnectionError("x"), health=health
        )
        record_degradation(
            RetrievalChannel.RERANK,
            "rerank_unavailable",
            ConnectionError("y"),
            severity=ChannelStatus.FAILED,
            health=health,
        )
        # A weaker status never downgrades a terminal one.
        record_degradation(RetrievalChannel.RERANK, "noise", None, health=health)
        # Non-channel components are counted but never reported as channels.
        record_degradation("audit_store", "audit_write_failed", RuntimeError("disk"), health=health)

    assert health.degraded_channels() == [
        degraded_channel(RetrievalChannel.RERANK, "rerank_unavailable", ChannelStatus.FAILED)
    ]
    assert health.failed_channels() == {"rerank"}
    assert _counter("rerank", "rerank_unavailable") >= 2
    assert _counter("audit_store", "audit_write_failed") >= 1
    assert all(row["channel"] != "audit_store" for row in health.degraded_channels())
    assert any("component=rerank" in message for message in warnings.messages)
    # Log lines stay free of the exception message: type only.
    assert not any("disk" in message for message in warnings.messages)


def test_cached_answer_still_reports_its_degraded_channels() -> None:
    """A replayed degraded answer must not come back looking clean (cache read)."""
    from agentic_graphrag.app.answer_cache import _is_cacheable_answer, _try_answer_cache
    from agentic_graphrag.core.reasoning import QueryStatus

    svc = _offline_service()
    try:
        assert svc.retrieval_cache is not None
        chain = ReasoningChain(question=QUESTION, status=QueryStatus.ANSWERED, answer="Apex.")
        health = ChannelHealth()
        for channel in (RetrievalChannel.VECTOR, RetrievalChannel.GRAPH):
            record_degradation(channel, "store_unavailable", None, severity="failed", health=health)
        assert health.failed_channels() == {"vector", "graph"}
        apply_degradation_policy(chain, health, _config_with(["vector", "graph"]))
        payload = chain.model_dump(mode="json")
        svc.retrieval_cache.set_answer(
            QUESTION,
            payload,
            tenant_id="default",
            user_id="anonymous",
            max_hops=None,
            force_agentic=False,
            timeout_ms=None,
        )
        replayed = _try_answer_cache(
            svc, QueryRequest(question=QUESTION), tenant_id="default", user_id="anonymous"
        )
    finally:
        svc.close()

    assert replayed is not None
    assert sorted(row.channel for row in replayed.degraded_channels) == ["graph", "vector"]
    assert replayed.low_confidence is True
    # ... and such an answer is deliberately not cacheable in the first place.
    assert _is_cacheable_answer(chain) is False


def test_prometheus_exposes_the_degradation_counter() -> None:
    record_degradation(RetrievalChannel.LLM, "llm_unavailable")
    text = prometheus_metrics_text()
    assert 'agr_degradation_total{component="llm",reason="llm_unavailable"}' in text
