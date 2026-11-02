"""Wiring, register loop, directed graph text and optional local adapter."""

import importlib
from types import SimpleNamespace

import httpx
import pytest

from agentic_graphrag.agent.executor_dispatch import handle_graph_neighbors, handle_graph_path
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import run_agentic_query, run_query
from agentic_graphrag.agent.register_search import search, seed_search
from agentic_graphrag.app.answer_cache import _is_cacheable_answer
from agentic_graphrag.app.wiring import build_executor_for_service
from agentic_graphrag.config import AppConfig, RerankConfig, Settings
from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.ports.stores import ChunkRecord, EntityRecord, PathRecord, RelationRecord
from agentic_graphrag.core.reasoning import QueryStatus, ReasoningChain
from agentic_graphrag.retrieval.cache import RetrievalCache
from agentic_graphrag.retrieval.graph_paths import path_candidates, score_paths
from agentic_graphrag.retrieval.rerank_local import LocalCrossEncoderReranker, _load_model
from agentic_graphrag.retrieval.rerank_text import candidate_text
from agentic_graphrag.retrieval.rerankers import build_reranker
from agentic_graphrag.stores.factory import create_bundle


def executor(tmp_path, *, allow_llm=True, kind="http"):
    cfg = AppConfig()
    cfg.paths.indexes_dir = str(tmp_path / "indexes")
    cfg.paths.processed_dir = str(tmp_path / "processed")
    cfg.retrieval.top_k = 2
    cfg.retrieval.rerank = RerankConfig(kind=kind, candidate_pool=30, endpoint="http://test")
    bundle = create_bundle("memory", "memory", cfg=cfg, load_bm25=False, load_embeddings=False)
    exe = build_executor_for_service(
        bundle=bundle,
        cfg=cfg,
        settings=Settings(llm_api_key="", _env_file=None),
        allow_llm=allow_llm,
        known_entities=[],
        retrieval_cache=RetrievalCache(),
        enable_cache=True,
    )
    return exe, bundle


@pytest.mark.parametrize("kind", ["http", "local", "lexical"])
def test_no_llm_wiring_disables_reranking(tmp_path, kind):
    exe, bundle = executor(tmp_path, allow_llm=False, kind=kind)
    assert exe.reranker is None
    assert exe.vector.top_k == exe.fulltext.top_k == 10
    assert exe.config.candidate_pool is None
    assert exe.cache is not None
    bundle.close()


def test_pool_reaches_all_channels_and_register_seed(monkeypatch, tmp_path):
    exe, bundle = executor(tmp_path)
    assert exe.vector.top_k == exe.fulltext.top_k == 30
    assert exe.fusion_limit == 2
    assert exe.graph.max_paths >= 30
    assert exe.cache is None  # final retrieval cache must not hide reranker recovery
    seen = []

    def neighbors(*a, **kw):
        seen.append(kw["limit"])
        return []

    monkeypatch.setattr(exe.graph, "neighbors", neighbors)
    monkeypatch.setattr(exe.graph, "paths", neighbors)
    handle_graph_neighbors(exe, {"entity": "Alice"}, "Alice")
    handle_graph_path(exe, {"source": "Alice", "target": "Acme"}, "Alice Acme")
    assert seen == [30, 30]
    candidates = [
        Candidate(id=str(i), source=CandidateSource.VECTOR, content=str(i)) for i in range(30)
    ]

    def vector_search(query, **kwargs):
        assert kwargs["top_k"] == 30
        return candidates

    monkeypatch.setattr(exe.vector, "search", vector_search)
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, json, **kw: httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json=[{"index": i, "score": float(t)} for i, t in enumerate(json["texts"])],
        ),
    )
    selected, _ = seed_search(exe, "q", top_k=2, tenant_id="", cache={})
    assert [c.id for c in selected] == ["29", "28"]
    bundle.close()


def test_register_repeated_chunk_scores_are_cached(monkeypatch, tmp_path):
    exe, bundle = executor(tmp_path)
    bundle.fulltext.index([ChunkRecord(chunk_id="c", doc_id="d", text="query", index=0)])
    calls = []

    def post(url, json, **kw):
        calls.append(json)
        return httpx.Response(
            200, request=httpx.Request("POST", url), json=[{"index": 0, "score": 1}]
        )

    monkeypatch.setattr(httpx, "post", post)
    for _ in range(2):
        search(exe, "query", {}, tenant_id="", anchors=[])
    assert len(calls) == 1
    assert exe.reranker.stats.cache_hits == 1
    bundle.close()


def test_path_and_neighbor_use_same_true_edge_direction():
    alice = EntityRecord(id="a", name="Alice", type="Person")
    acme = EntityRecord(id="b", name="Acme", type="Company")
    rel = RelationRecord(id="r", type="CEO_OF", head_id="a", tail_id="b")
    path = PathRecord(nodes=[acme, alice], relations=[rel], length=1)
    candidate = path_candidates(score_paths([path], None), "Acme", "Alice")[0]
    neighbor = Candidate(
        id="g",
        source=CandidateSource.GRAPH_NEIGHBOR,
        content="",
        structured={"head": "Alice", "relation": "CEO_OF", "tail": "Acme"},
    )
    assert candidate_text(candidate) == candidate_text(neighbor) == "Alice ceo of Acme."
    assert "<-[CEO_OF#r]-" in candidate.content
    legacy = Candidate(
        id="old", source=CandidateSource.GRAPH_PATH, content="Acme <-[CEO_OF#r]- Alice"
    )
    assert candidate_text(legacy) == "Acme <-[CEO_OF]- Alice"


def test_local_adapter_loads_lazily_and_bounds_pairs(monkeypatch):
    _load_model.cache_clear()
    calls = []

    class Model:
        def __init__(self, model, **kwargs):
            calls.append((model, kwargs))

        def predict(self, pairs, **kwargs):
            assert pairs == [("qq", "aaa"), ("qq", "bbb")]
            assert kwargs["batch_size"] == 2
            return [0.1, 0.8]

    def load(name):
        assert name == "sentence_transformers"
        return SimpleNamespace(CrossEncoder=Model)

    monkeypatch.setattr(importlib, "import_module", load)
    cfg = RerankConfig(
        kind="local", model="test", max_chars=3, max_query_chars=2, batch_size=2, max_length=64
    )
    rr = build_reranker(cfg)
    assert isinstance(rr, LocalCrossEncoderReranker)
    assert not calls
    candidates = [
        Candidate(id=t, source=CandidateSource.VECTOR, content=t * 10) for t in ["a", "b"]
    ]
    assert [c.id for c in rr.rerank("qqq", candidates)] == ["b", "a"]
    assert calls == [("test", {"max_length": 64, "trust_remote_code": False})]
    _load_model.cache_clear()


def test_missing_local_dependency_has_actionable_error(monkeypatch):
    _load_model.cache_clear()

    def missing(name):
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(RuntimeError, match="rerank-local"):
        _load_model("missing", 512)


def test_degraded_answer_cannot_be_cached():
    chain = ReasoningChain(
        question="q", answer="a", status=QueryStatus.ANSWERED, metadata={"rerank_degraded": True}
    )
    assert not _is_cacheable_answer(chain)


@pytest.mark.parametrize("entry", [run_query, run_agentic_query])
def test_degradation_and_score_cache_are_scoped_to_query(monkeypatch, tmp_path, entry):
    exe, bundle = executor(tmp_path)
    bundle.fulltext.index([ChunkRecord(chunk_id="c", doc_id="d", text="query", index=0)])

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("down")

    monkeypatch.setattr(httpx, "post", timeout)
    guards = GuardrailConfig(max_hops=1)
    failed = entry("query", exe, None, allow_llm=False, guard_cfg=guards)
    assert failed.metadata["rerank_degraded"] is True
    assert failed.metadata["rerank"]["fallbacks"] >= 1
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kw: httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json=[{"index": 0, "score": 1}],
        ),
    )
    # Recovery succeeds without retaining the previous run's failure counters.
    exe.reranker.circuit.record_success()
    recovered = entry("query", exe, None, allow_llm=False, guard_cfg=guards)
    assert recovered.metadata["rerank_degraded"] is False
    assert recovered.metadata["rerank"]["requests"] == 1
    bundle.close()
