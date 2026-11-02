"""Offline upstream failures, batching, graph policies and per-run score reuse."""

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from agentic_graphrag.config import RerankConfig
from agentic_graphrag.core.evidence import Candidate, CandidateSource, Citation
from agentic_graphrag.retrieval.fusion import fuse_candidates
from agentic_graphrag.retrieval.rerank_http import HttpCrossEncoderReranker
from agentic_graphrag.retrieval.rerankers import _shared_circuit, build_reranker


def hits():
    return [Candidate(id=str(i), source=CandidateSource.VECTOR, content=str(i)) for i in range(3)]


def response(url, rows, status=200):
    return httpx.Response(status, json=rows, request=httpx.Request("POST", url))


@pytest.mark.parametrize("failure,attempts", [("timeout", 1), ("500", 2), ("400", 1)])
def test_failure_preserves_rrf_and_records_warning(monkeypatch, caplog, failure, attempts):
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs)
        if failure == "timeout":
            raise httpx.ReadTimeout("private query must not be logged")
        return response(url, [], status=int(failure))

    monkeypatch.setattr(httpx, "post", post)
    rr = HttpCrossEncoderReranker("http://r")
    expected = fuse_candidates(hits(), list(reversed(hits())))
    actual = fuse_candidates(hits(), list(reversed(hits())), reranker=rr)
    assert [(c.id, c.score) for c in actual] == [(c.id, c.score) for c in expected]
    assert len(calls) == attempts
    assert rr.fallbacks == 1
    assert all(c.metadata.get("rerank_fallback") for c in actual)
    assert "preserving RRF order" in caplog.text
    assert "private query" not in caplog.text


def test_retry_once_then_recover(monkeypatch):
    attempts = []

    def post(url, **kwargs):
        attempts.append(kwargs)
        status = 503 if len(attempts) == 1 else 200
        return response(url, [{"index": i, "score": i} for i in range(3)], status)

    monkeypatch.setattr(httpx, "post", post)
    rr = HttpCrossEncoderReranker("http://r")
    assert [c.id for c in rr.rerank("q", hits())] == ["2", "1", "0"]
    assert len(attempts) == 2
    assert rr.fallbacks == 0


def test_batches_truncate_both_inputs_and_preserve_evidence(monkeypatch):
    calls = []

    def post(url, json, timeout):
        calls.append(json)
        assert json["query"] == "que"
        assert all(len(t) <= 2 for t in json["texts"])
        assert json["truncate"] is True
        assert timeout == 0.25
        return response(url, [{"index": i, "score": float(t)} for i, t in enumerate(json["texts"])])

    monkeypatch.setattr(httpx, "post", post)
    cfg = RerankConfig(batch_size=2, max_chars=2, max_query_chars=3, timeout_seconds=0.25)
    rr = HttpCrossEncoderReranker("http://r", cfg=cfg)
    candidates = [c.model_copy(update={"content": c.content * 100}) for c in hits()]
    out = rr.rerank("query", candidates)
    assert [c.id for c in out] == ["2", "1", "0"]
    assert len(calls) == 2
    assert all(len(c.content) == 100 for c in out)


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"index": 0, "score": 1}],
        [{"index": 0, "score": 1}] * 3,
        [{"index": i, "score": "nan"} for i in range(3)],
        [{"index": i + 1, "score": 1} for i in range(3)],
        [{"index": i} for i in range(3)],
    ],
)
def test_invalid_scores_fall_back_atomically(monkeypatch, rows):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: response(url, rows))
    rr = HttpCrossEncoderReranker("http://r")
    assert [c.id for c in rr.rerank("q", hits())] == ["0", "1", "2"]
    assert rr.fallbacks == 1
    assert not rr._scores


def test_later_batch_failure_does_not_commit_partial_scores(monkeypatch):
    calls = []

    def post(url, **kw):
        calls.append(kw)
        if len(calls) > 1:
            raise httpx.ReadTimeout("down")
        return response(url, [{"index": 0, "score": 99}])

    monkeypatch.setattr(httpx, "post", post)
    rr = HttpCrossEncoderReranker("http://r", cfg=RerankConfig(batch_size=1))
    assert [c.id for c in rr.rerank("q", hits())] == ["0", "1", "2"]
    assert not rr._scores


def test_score_cache_reuses_chunks_across_calls_and_parallel_branches(monkeypatch):
    calls = []

    def post(url, json, **kw):
        calls.append(json)
        return response(url, [{"index": i, "score": float(t)} for i, t in enumerate(json["texts"])])

    monkeypatch.setattr(httpx, "post", post)
    rr = HttpCrossEncoderReranker("http://r")
    candidates = hits()
    candidates[0].citations = [Citation(chunk_id="shared")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: rr.rerank("q", candidates), range(2)))
    alias = candidates[0].model_copy(update={"id": "other-channel"})
    rr.rerank("q", [alias])
    assert len(calls) == 1
    rr.rerank("other query", candidates)
    assert len(calls) == 2
    candidates[0].content = "4"
    rr.rerank("q", candidates)
    assert calls[-1]["texts"] == ["4"]
    rr.reset_run()
    rr.rerank("q", candidates)
    assert len(calls) == 4


def test_circuit_shared_between_runs_but_scores_are_not(monkeypatch):
    _shared_circuit.cache_clear()
    calls = []
    now = [100.0]
    monkeypatch.setattr("agentic_graphrag.llm.circuit.time.time", lambda: now[0])

    def post(url, **kw):
        calls.append(kw)
        if now[0] == 100:
            raise httpx.ConnectError("down")
        return response(url, [{"index": i, "score": i} for i in range(3)])

    monkeypatch.setattr(httpx, "post", post)
    cfg = RerankConfig(kind="http", endpoint="http://unique", failure_threshold=2)
    for _ in range(3):
        rr = build_reranker(cfg)
        rr.rerank("q", hits())
    assert len(calls) == 2
    assert rr.fallbacks == 1
    now[0] += 21
    assert [c.id for c in rr.rerank("q", hits())] == ["2", "1", "0"]
    build_reranker(cfg).rerank("q", hits())
    assert len(calls) == 4  # recovered, but new run does not reuse private scores
    _shared_circuit.cache_clear()


@pytest.mark.parametrize(
    "policy,expected",
    [
        ("exempt", ["2", "graph", "0"]),
        ("floor", ["graph", "2", "0"]),
        ("score", ["2", "0", "graph"]),
    ],
)
def test_graph_policy_after_rrf(monkeypatch, policy, expected):
    posted = []

    def post(url, json, **kw):
        posted.extend(json["texts"])
        return response(
            url,
            [
                {"index": i, "score": -5 if "ceo of" in text else float(text)}
                for i, text in enumerate(json["texts"])
            ],
        )

    monkeypatch.setattr(httpx, "post", post)
    graph = Candidate(
        id="graph",
        source=CandidateSource.GRAPH_NEIGHBOR,
        content="raw",
        structured={"head": "Alice", "relation": "CEO_OF", "tail": "Acme"},
    )
    # Fused candidates must still be recognized by origin_source.
    graph = fuse_candidates([graph])[0]
    candidates = [hits()[0], graph, hits()[2]]
    rr = HttpCrossEncoderReranker("http://r", cfg=RerankConfig(graph_policy=policy, graph_floor=3))
    out = rr.rerank("q", candidates)
    assert [c.id for c in out] == expected
    assert ("Alice ceo of Acme." in posted) == (policy != "exempt")
    assert graph.content == "raw"


def test_graph_only_exemption_never_calls_http(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: pytest.fail("unexpected HTTP"))
    graph = Candidate(id="g", source=CandidateSource.GRAPH_PATH, content="A -> B")
    rr = HttpCrossEncoderReranker("http://r")
    assert rr.rerank("q", [graph])[0].metadata["rerank_exempt"] is True
    assert rr.rerank("q", []) == []
