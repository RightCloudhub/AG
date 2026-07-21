"""Unit tests for production relation embed scorer (P2-RT-01)."""

from __future__ import annotations

from agentic_graphrag.llm.provider import MockLLMProvider
from agentic_graphrag.retrieval.relation_embed import cosine_sim, make_relation_embed_sim


def test_cosine_sim_identical():
    v = [1.0, 0.0, 0.0]
    assert cosine_sim(v, v) == 1.0


def test_cosine_sim_orthogonal_is_zero():
    assert cosine_sim([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_make_relation_embed_sim_returns_float():
    llm = MockLLMProvider(embedding_dim=8)
    score = make_relation_embed_sim(llm)
    val = score("CEO_OF", "Who is the CEO of Apex?")
    assert val is not None
    assert 0.0 <= val <= 1.0


def test_make_relation_embed_sim_empty_returns_none():
    llm = MockLLMProvider()
    score = make_relation_embed_sim(llm)
    assert score("CEO_OF", "") is None
    assert score("", "question") is None


def test_make_relation_embed_sim_caches_embeds():
    llm = MockLLMProvider(embedding_dim=8)
    cache: dict[str, list[float]] = {}
    score = make_relation_embed_sim(llm, cache=cache)
    score("PARENT_OF", "parent company of BrightLink")
    assert len(cache) >= 1
