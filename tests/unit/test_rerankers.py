"""Two-stage retrieval re-rankers (ARCH_OPTIMIZATION_V2 O-5)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from agentic_graphrag.app.wiring import _recall_sizes
from agentic_graphrag.config import RetrievalConfig
from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.retrieval.fusion import fuse_candidates
from agentic_graphrag.retrieval.rerankers import (
    HttpCrossEncoderReranker,
    LexicalReranker,
    build_reranker,
)


def _c(cid: str, text: str, src=CandidateSource.VECTOR) -> Candidate:
    return Candidate(id=cid, source=src, content=text, score=0.0)


def test_lexical_prefers_query_coverage_and_keeps_ties_stable() -> None:
    cands = [_c("off", "Apex Holdings annual picnic"), _c("on", "Elena Varga CEO of Apex Holdings")]
    out = LexicalReranker().rerank("Who is the CEO of Apex Holdings?", cands)
    assert [c.id for c in out] == ["on", "off"]
    assert out[0].metadata["rerank"] == "lexical"
    same = [_c("x", "foo"), _c("y", "foo")]
    assert [c.id for c in LexicalReranker().rerank("foo", same)] == ["x", "y"]
    assert LexicalReranker().rerank("the of", cands) == cands  # no content terms


def test_fuse_candidates_applies_reranker_before_cut() -> None:
    a = [_c("a1", "noise"), _c("a2", "elena varga ceo")]
    b = [_c("b1", "more noise", CandidateSource.FULLTEXT)]
    out = fuse_candidates(a, b, query="elena varga ceo", limit=1, reranker=LexicalReranker())
    assert [c.id for c in out] == ["a2"]


def test_http_reranker_orders_by_server_scores(monkeypatch) -> None:
    def fake_post(url, json, timeout):
        assert json["texts"] == ["first", "second"]
        req = httpx.Request("POST", url)
        return httpx.Response(
            200, json=[{"index": 1, "score": 0.9}, {"index": 0, "score": 0.1}], request=req
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    rr = HttpCrossEncoderReranker("http://rerank/rerank")
    out = rr.rerank("q", [_c("0", "first"), _c("1", "second")])
    assert [c.id for c in out] == ["1", "0"]
    assert out[0].metadata["rerank"] == "cross_encoder"


def test_http_reranker_wrapped_results_and_fallback(monkeypatch) -> None:
    req = httpx.Request("POST", "http://r")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200, json={"results": [{"index": 0, "relevance_score": 2.0}]}, request=req
        ),
    )
    rr = HttpCrossEncoderReranker("http://r")
    assert [c.id for c in rr.rerank("q", [_c("0", "a"), _c("1", "b")])] == ["0", "1"]

    def boom(*a, **k):
        raise httpx.ConnectTimeout("down")

    monkeypatch.setattr(httpx, "post", boom)
    out = rr.rerank("q", [_c("0", "a"), _c("1", "b")])
    assert [c.id for c in out] == ["0", "1"]
    assert out[0].metadata["rerank_fallback"] == "ConnectTimeout"
    assert rr.fallbacks == 1
    assert rr.rerank("q", [_c("only", "x")])[0].id == "only"


def test_build_reranker_and_recall_sizes() -> None:
    assert build_reranker(SimpleNamespace(kind="none")) is None
    assert isinstance(build_reranker(SimpleNamespace(kind="lexical")), LexicalReranker)
    http = build_reranker(SimpleNamespace(kind="http", endpoint="http://x", timeout_seconds=1))
    assert isinstance(http, HttpCrossEncoderReranker)
    with pytest.raises(ValueError):
        build_reranker(SimpleNamespace(kind="magic"))
    with pytest.raises(ValueError):
        HttpCrossEncoderReranker("")
    ret = RetrievalConfig()
    assert _recall_sizes(ret, reranked=False) == (10, 10)
    assert _recall_sizes(ret, reranked=True) == (30, 30)
