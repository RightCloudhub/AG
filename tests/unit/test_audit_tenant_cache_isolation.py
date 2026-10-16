"""Multi-tenant answer cache + audit isolation (security fixes)."""

from __future__ import annotations

from agentic_graphrag.generation.audit_store import AuditStore
from agentic_graphrag.generation.trace import QueryStatus, ReasoningChain
from agentic_graphrag.retrieval.cache import RetrievalCache


def test_answer_cache_is_tenant_scoped() -> None:
    cache = RetrievalCache()
    cache.set_answer(
        "Who is CEO?",
        {"query_id": "q1", "answer": "A", "metadata": {"tenant_id": "t1"}},
        tenant_id="t1",
        user_id="u1",
        max_hops=3,
    )
    # Same question, different tenant → miss
    assert cache.get_answer("Who is CEO?", tenant_id="t2", user_id="u1", max_hops=3) is None
    # Same tenant hits
    hit = cache.get_answer("Who is CEO?", tenant_id="t1", user_id="u1", max_hops=3)
    assert hit is not None
    assert hit["answer"] == "A"
    # Different max_hops → miss
    assert cache.get_answer("Who is CEO?", tenant_id="t1", user_id="u1", max_hops=5) is None


def test_audit_get_for_tenant() -> None:
    store = AuditStore()
    chain = ReasoningChain(
        question="q",
        answer="a",
        status=QueryStatus.ANSWERED,
        metadata={"tenant_id": "acme"},
    )
    store.save(chain)
    assert store.get_for_tenant(chain.query_id, "acme") is not None
    assert store.get_for_tenant(chain.query_id, "other") is None
    # Missing tenant on legacy rows only visible to default
    legacy = ReasoningChain(question="q2", answer="b", status=QueryStatus.ANSWERED)
    store.save(legacy)
    assert store.get_for_tenant(legacy.query_id, "default") is not None
    assert store.get_for_tenant(legacy.query_id, "acme") is None
