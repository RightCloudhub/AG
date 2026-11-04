"""Degraded retrieval and answers must not survive an outage in the cache."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import Mock

import pytest

from agentic_graphrag.agent.executor import Executor, ToolCallSpec
from agentic_graphrag.app.answer_cache import _maybe_cache_answer, _try_answer_cache
from agentic_graphrag.app.contracts import QueryRequest, QueryResultData
from agentic_graphrag.app.degradation_policy import apply_degradation_policy
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.app.wiring import build_executor_for_service
from agentic_graphrag.config import get_config
from agentic_graphrag.core.enums import RetrievalChannel, RetrievalTool
from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.reasoning import QueryStatus, ReasoningChain
from agentic_graphrag.observability.degradation import CHANNELS, ChannelHealth, ChannelStatus
from agentic_graphrag.retrieval.fulltext import FulltextRetriever
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.retrieval.rerankers import HttpCrossEncoderReranker
from agentic_graphrag.retrieval.vector import VectorRetriever

QUESTION = "Who is the CEO of the parent company of NovaTech Industries?"
FULLTEXT = [
    Candidate(
        id="fulltext-parent",
        source=CandidateSource.FULLTEXT_CHUNK,
        content="NovaTech Industries is a subsidiary of Apex Holdings.",
        score=1.0,
    ),
    Candidate(
        id="fulltext-ceo",
        source=CandidateSource.FULLTEXT_CHUNK,
        content="Elena Varga is the CEO of Apex Holdings.",
        score=0.9,
    ),
]


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Iterator[QueryService]:
    svc = QueryService.create_offline(cfg=get_config().model_copy(deep=True))
    svc.enable_cache = True
    specs = [
        ToolCallSpec(tool=RetrievalTool.VECTOR_SEARCH),
        ToolCallSpec(tool=RetrievalTool.GRAPH_NEIGHBORS, args={"entity": "NovaTech Industries"}),
        ToolCallSpec(tool=RetrievalTool.FULLTEXT_SEARCH),
    ]
    monkeypatch.setattr(Executor, "_choose_tools", Mock(return_value=specs))
    monkeypatch.setattr(FulltextRetriever, "search", Mock(return_value=FULLTEXT))
    try:
        yield svc
    finally:
        svc.close()


def _executor(svc: QueryService) -> Executor:
    return build_executor_for_service(
        bundle=svc.bundle,
        cfg=svc.cfg,
        settings=svc.settings,
        allow_llm=False,
        known_entities=svc.known_entities,
        retrieval_cache=svc.retrieval_cache,
        enable_cache=True,
        tenant_id="default",
    )


def _hit_ids(data: QueryResultData) -> set[str]:
    return {hit for step in data.steps for tool in step.tool_calls for hit in tool.hits}


@pytest.mark.parametrize("parallel", [False, True])
def test_repeated_queries_keep_outage_visible(
    service: QueryService, monkeypatch: pytest.MonkeyPatch, parallel: bool
) -> None:
    service.cfg.retrieval.parallel = parallel
    vector = Mock(side_effect=ConnectionError("vector down"))
    graph = Mock(side_effect=ConnectionError("graph down"))
    monkeypatch.setattr(VectorRetriever, "search", vector)
    monkeypatch.setattr(GraphRetriever, "neighbors", graph)
    req = QueryRequest(question=QUESTION, force_agentic=True, max_hops=1)

    first = service.run_query(req)
    calls = (vector.call_count, graph.call_count)
    second = service.run_query(req)

    for data in (first, second):
        assert data.low_confidence is True
        assert {row.channel for row in data.degraded_channels} == {"vector", "graph"}
        assert all(row.status == ChannelStatus.FAILED for row in data.degraded_channels)
        assert _hit_ids(data) == {c.id for c in FULLTEXT}
    assert vector.call_count > calls[0] > 0
    assert graph.call_count > calls[1] > 0
    assert service.retrieval_cache is not None
    assert service.retrieval_cache.retrieval.stats()["size"] == 0
    assert service.retrieval_cache.answers.stats()["size"] == 0


@pytest.mark.parametrize("parallel", [False, True])
def test_recovery_retrieves_complete_candidates_then_caches_them(
    service: QueryService, monkeypatch: pytest.MonkeyPatch, parallel: bool
) -> None:
    service.cfg.retrieval.parallel = parallel
    vector = Mock(side_effect=ConnectionError("vector down"))
    graph = Mock(side_effect=ConnectionError("graph down"))
    monkeypatch.setattr(VectorRetriever, "search", vector)
    monkeypatch.setattr(GraphRetriever, "neighbors", graph)
    req = QueryRequest(question=QUESTION, force_agentic=True, max_hops=1)
    degraded = service.run_query(req)
    assert degraded.low_confidence is True
    assert _hit_ids(degraded) == {c.id for c in FULLTEXT}

    vector.side_effect = graph.side_effect = None
    vector.return_value = [FULLTEXT[0].model_copy(update={"id": "vector-recovered"})]
    graph.return_value = [FULLTEXT[1].model_copy(update={"id": "graph-recovered"})]
    recovered = service.run_query(req)

    assert recovered.degraded_channels == []
    assert recovered.low_confidence is False
    assert _hit_ids(recovered) == {c.id for c in FULLTEXT} | {"vector-recovered", "graph-recovered"}
    calls = (vector.call_count, graph.call_count)
    replayed = service.run_query(req)
    assert _hit_ids(replayed) == _hit_ids(recovered)
    assert (vector.call_count, graph.call_count) == calls
    assert any(
        tool.tool == RetrievalTool.CACHE for step in replayed.steps for tool in step.tool_calls
    )


@pytest.mark.parametrize("channel", [RetrievalChannel.EMBEDDING, RetrievalChannel.RERANK])
def test_prior_fallback_blocks_later_subquery_cache_writes(
    service: QueryService, monkeypatch: pytest.MonkeyPatch, channel: RetrievalChannel
) -> None:
    monkeypatch.setattr(VectorRetriever, "search", Mock(return_value=[]))
    monkeypatch.setattr(GraphRetriever, "neighbors", Mock(return_value=[]))
    executor = _executor(service)
    executor.health.mark(channel, ChannelStatus.DEGRADED, "fallback")

    for question in ("first subquestion", "second subquestion"):
        candidates, _ = executor.run(question, allow_llm=False, tenant_id="default")
        assert {c.id for c in candidates} == {c.id for c in FULLTEXT}
    assert executor.health.failed_channels() == set()
    assert service.retrieval_cache is not None
    assert service.retrieval_cache.retrieval.stats()["size"] == 0


def test_reranker_fallback_blocks_cache_write_after_fusion(
    service: QueryService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(VectorRetriever, "search", Mock(return_value=[]))
    monkeypatch.setattr(GraphRetriever, "neighbors", Mock(return_value=[]))
    executor = _executor(service)
    reranker = HttpCrossEncoderReranker("https://rerank.invalid", health=executor.health)
    score = Mock(side_effect=TimeoutError("reranker down"))
    monkeypatch.setattr(reranker, "_score", score)
    executor.reranker = reranker

    for _ in range(2):
        candidates, _ = executor.run(QUESTION, allow_llm=False, tenant_id="default")
        assert {c.id for c in candidates} == {c.id for c in FULLTEXT}
        assert all(c.metadata["rerank_fallback"] == "TimeoutError" for c in candidates)
    assert score.call_count == 2
    assert executor.health.failed_channels() == set()
    assert service.retrieval_cache is not None
    assert service.retrieval_cache.retrieval.stats()["size"] == 0


@pytest.mark.parametrize("channel", sorted(CHANNELS))
@pytest.mark.parametrize("status", [ChannelStatus.DEGRADED, ChannelStatus.FAILED])
def test_single_channel_degradation_never_enters_answer_cache(
    service: QueryService, channel: str, status: str
) -> None:
    req = QueryRequest(question=QUESTION)
    chain = ReasoningChain(question=QUESTION, status=QueryStatus.ANSWERED, answer="Elena Varga")
    health = ChannelHealth()
    health.mark(channel, status, "fallback")
    apply_degradation_policy(chain, health, service.cfg)
    assert not chain.metadata.get("low_confidence")

    _maybe_cache_answer(service, req, chain, tenant_id="default", user_id="anonymous")
    assert _try_answer_cache(service, req, tenant_id="default", user_id="anonymous") is None

    healthy = ReasoningChain(question=QUESTION, status=QueryStatus.ANSWERED, answer="Elena Varga")
    from agentic_graphrag.app.query_persistence import persist_chain

    persist_chain(service, healthy)
    _maybe_cache_answer(service, req, healthy, tenant_id="default", user_id="anonymous")
    replayed = _try_answer_cache(service, req, tenant_id="default", user_id="anonymous")
    assert replayed is not None
    assert replayed.degraded_channels == []
    assert replayed.low_confidence is False


@pytest.mark.parametrize("status", [ChannelStatus.DEGRADED, ChannelStatus.FAILED])
def test_health_keeps_degradation_for_the_whole_run(status: str) -> None:
    health = ChannelHealth()
    assert health.is_degraded() is False
    health.mark(RetrievalChannel.VECTOR, ChannelStatus.OK)
    assert health.is_degraded() is False
    health.mark(RetrievalChannel.EMBEDDING, status, "fallback")
    assert health.is_degraded() is True
    health.mark(RetrievalChannel.EMBEDDING, ChannelStatus.OK)
    assert health.is_degraded() is True
