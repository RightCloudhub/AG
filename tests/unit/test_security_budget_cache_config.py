"""Fixes: executor config wiring, x-user-id budget identity, answer-cache policy."""

from __future__ import annotations

from unittest.mock import MagicMock

from agentic_graphrag.api.auth import (
    AuthRateLimitMiddleware,
    trust_x_user_id_enabled,
    user_id_for_api_key,
)
from agentic_graphrag.api.service_helpers import build_executor_for_service
from agentic_graphrag.api.service_query import _is_cacheable_answer, _maybe_cache_answer
from agentic_graphrag.config import AppConfig, RetrievalConfig, Settings
from agentic_graphrag.generation.trace import QueryStatus, ReasoningChain
from agentic_graphrag.retrieval.cache import RetrievalCache
from agentic_graphrag.stores.doc_store import InMemoryDocStore
from agentic_graphrag.stores.factory import GraphBackend, StoreBundle, VectorBackend
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore


def test_build_executor_reads_retrieval_config() -> None:
    cfg = AppConfig(retrieval=RetrievalConfig(parallel=False, fusion_method="concat", fusion_k=42))
    bundle = StoreBundle(
        graph=InMemoryGraphStore(),
        vector=InMemoryVectorStore(),
        fulltext=BM25FulltextStore(),
        docs=InMemoryDocStore(),
        graph_backend=GraphBackend.MEMORY,
        vector_backend=VectorBackend.MEMORY,
    )
    ex = build_executor_for_service(
        bundle=bundle,
        cfg=cfg,
        settings=Settings(),
        allow_llm=False,
        known_entities=[],
        retrieval_cache=None,
        enable_cache=False,
    )
    assert ex.parallel is False
    assert ex.fusion_method == "concat"
    assert ex.fusion_k == 42


def test_user_id_bound_to_api_key_not_header(monkeypatch) -> None:
    monkeypatch.delenv("AGR_TRUST_X_USER_ID", raising=False)
    assert trust_x_user_id_enabled() is False
    key = "secret-key-1"
    mw = AuthRateLimitMiddleware(app=MagicMock(), api_keys={key: "acme"}, require_auth=True)
    req = MagicMock()
    req.headers = {"x-user-id": "attacker-minted-user"}
    principal = mw._principal_for(key, req)
    assert principal.user_id == user_id_for_api_key(key)
    assert principal.user_id != "attacker-minted-user"
    # Same key always same budget identity.
    assert mw._principal_for(key, req).user_id == principal.user_id


def test_trust_x_user_id_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("AGR_TRUST_X_USER_ID", "1")
    assert trust_x_user_id_enabled() is True
    key = "secret-key-2"
    mw = AuthRateLimitMiddleware(app=MagicMock(), api_keys={key: "acme"}, require_auth=True)
    req = MagicMock()
    req.headers = {"x-user-id": "alice"}
    assert mw._principal_for(key, req).user_id == "alice"


def test_answer_cache_skips_no_answer_and_partial() -> None:
    cache = RetrievalCache()
    svc = MagicMock()
    svc.retrieval_cache = cache
    req = MagicMock()
    req.question = "Who?"
    req.max_hops = 3
    req.force_agentic = False
    req.timeout_ms = None

    no = ReasoningChain(question="Who?", status=QueryStatus.NO_ANSWER, answer="无法回答")
    partial = ReasoningChain(question="Who?", status=QueryStatus.PARTIAL, answer="maybe")
    degraded = ReasoningChain(
        question="Who?",
        status=QueryStatus.ANSWERED,
        answer="Elena Varga",
        metadata={"llm_degraded": True},
    )
    ok = ReasoningChain(
        question="Who?",
        status=QueryStatus.ANSWERED,
        answer="Elena Varga",
    )

    assert _is_cacheable_answer(no) is False
    assert _is_cacheable_answer(partial) is False
    assert _is_cacheable_answer(degraded) is False
    assert _is_cacheable_answer(ok) is True

    for chain in (no, partial, degraded):
        _maybe_cache_answer(svc, req, chain, tenant_id="t", user_id="u")
    assert cache.get_answer("Who?", tenant_id="t", user_id="u", max_hops=3) is None

    _maybe_cache_answer(svc, req, ok, tenant_id="t", user_id="u")
    hit = cache.get_answer("Who?", tenant_id="t", user_id="u", max_hops=3)
    assert hit is not None
    assert hit["answer"] == "Elena Varga"
