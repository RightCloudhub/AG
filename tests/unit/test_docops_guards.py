"""Fences around document operations: review guard, busy leases, bundle wiring."""

from docops_support import make_env

from agentic_graphrag.api.routes.document_mutations import document_capabilities
from agentic_graphrag.core.enums import IngestStatus
from agentic_graphrag.core.ports.visibility import AllVisible
from agentic_graphrag.knowledge.index_pipeline import DOCUMENT_BUSY
from agentic_graphrag.knowledge.review.document_guard import stale_document
from agentic_graphrag.knowledge.review.queue import ReviewItem
from agentic_graphrag.stores.chunk_visibility import DocStoreVisibility, chunk_visibility_for
from agentic_graphrag.stores.doc_store import InMemoryDocStore


def review(did, cid, revision):
    triple = {
        "source_doc_id": did,
        "source_chunk_id": cid,
        "source_doc_version": 1,
        "source_chunk_revision": revision,
    }
    return ReviewItem(id="r", type="conflict", payload={"incoming": triple}, tenant_id="alpha")


def test_review_decisions_are_fenced_by_chunk_revision_and_visibility(tmp_path):
    env = make_env(tmp_path)
    did = env.upload()
    cid = env.docs.get(did, tenant_id="alpha").chunks[0].chunk_id
    assert not stale_document(review(did, cid, 1), env.docs)
    env.patch(did, 0, key="edit", text="Acme PRODUCES Gizmo.")
    assert stale_document(review(did, cid, 1), env.docs)
    assert not stale_document(review(did, cid, 2), env.docs)
    env.patch(did, 0, key="off", enabled=False)
    assert stale_document(review(did, cid, 3), env.docs)
    assert stale_document(review(did, "missing", 1), env.docs)
    graph_sources = [s for r in env.graph.list_relations() for s in r.sources]
    assert all(s.get("revision") for s in graph_sources)  # revision-scoped provenance


def test_busy_document_lease_requeues_the_task_instead_of_failing(tmp_path):
    env = make_env(tmp_path)
    did = env.upload()
    outcome = env.patch(did, 0, key="edit", text="Acme PRODUCES Gizmo.")
    holder = env.docs.acquire_lease(("alpha", did), owner="someone-else", ttl=60)
    assert env.worker.run_once() == []
    task = env.tasks.get(outcome.op.task_id)
    assert task.status == IngestStatus.QUEUED and "lease" in task.message
    assert env.worker.pipeline.process_or_busy(did, tenant_id="alpha").error == DOCUMENT_BUSY
    assert [r.error for r in env.worker.reindex(tenant_id="alpha")] == [DOCUMENT_BUSY]
    env.docs.release_lease(holder)
    env.worker.run_once()
    assert env.tasks.get(outcome.op.task_id).status == IngestStatus.DONE


def test_task_store_ensure_is_idempotent_and_records_outcomes(tmp_path):
    env = make_env(tmp_path)
    first = env.tasks.ensure("alpha", [{"doc_id": "a"}], task_id="t")
    again = env.tasks.ensure("alpha", [{"doc_id": "other"}], task_id="t")
    assert again.docs == first.docs == [{"doc_id": "a"}]
    env.tasks.update_docs("t", [{"doc_id": "b", "provisional_doc_id": "a"}])
    assert env.tasks.get("t").docs[0]["provisional_doc_id"] == "a"


def test_bundle_visibility_and_capabilities_follow_the_doc_store(monkeypatch, tmp_path):
    from agentic_graphrag.app.query_service import QueryService

    monkeypatch.setenv("AGR_RUNTIME_DIR", str(tmp_path))
    svc = QueryService.create_offline()
    try:
        assert isinstance(svc.bundle.visibility, DocStoreVisibility)
        assert all(document_capabilities(svc, operator=True).values())
        assert not any(document_capabilities(svc, operator=False).values())
        svc.bundle.docs = InMemoryDocStore()
        assert not any(document_capabilities(svc, operator=True).values())
        assert not any(document_capabilities(None, operator=True).values())
    finally:
        svc.close()
    assert isinstance(chunk_visibility_for(InMemoryDocStore()), AllVisible)
