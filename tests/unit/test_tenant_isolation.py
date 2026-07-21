"""Tenant cache isolation + audit AuthZ (P4-REL-01)."""

from __future__ import annotations

from agentic_graphrag.api.service import QueryService
from agentic_graphrag.generation.audit_store import AuditStore
from agentic_graphrag.retrieval.cache import RetrievalCache


def test_answer_cache_keyed_by_tenant():
    cache = RetrievalCache()
    cache.set_answer("Who?", {"query_id": "a", "answer": "A"}, tenant_id="t1")
    cache.set_answer("Who?", {"query_id": "b", "answer": "B"}, tenant_id="t2")
    assert cache.get_answer("Who?", tenant_id="t1")["answer"] == "A"
    assert cache.get_answer("Who?", tenant_id="t2")["answer"] == "B"
    assert cache.answer_key("Who?", tenant_id="t1") != cache.answer_key(
        "Who?", tenant_id="t2"
    )


def test_audit_for_tenant_filters_cross_tenant(tmp_path):
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)
    store.save(
        {
            "query_id": "q1",
            "question": "x",
            "metadata": {"tenant_id": "alpha"},
            "answer": "yes",
        }
    )
    svc = QueryService.create_offline()
    svc.audit_store = store
    assert svc.get_audit_for_tenant("q1", tenant_id="alpha") is not None
    assert svc.get_audit_for_tenant("q1", tenant_id="beta") is None


def test_feedback_denied_cross_tenant(tmp_path):
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)
    store.save(
        {
            "query_id": "q2",
            "question": "x",
            "metadata": {"tenant_id": "alpha"},
            "answer": "yes",
        }
    )
    svc = QueryService.create_offline()
    svc.audit_store = store
    try:
        svc.submit_feedback("q2", accurate=False, tenant_id="beta")
        raised = False
    except PermissionError:
        raised = True
    assert raised
