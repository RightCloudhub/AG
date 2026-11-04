"""Publication is the commit point for task completion and review recovery."""

import pytest
from test_knowledge_workflow import root_service as root_service
from test_knowledge_workflow import upload

from agentic_graphrag.app.documents import lifecycle_for
from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.app.knowledge_writes import prepare_upload
from agentic_graphrag.app.query_selection import select_documents
from agentic_graphrag.app.review_app import ReviewApp
from agentic_graphrag.app.write_path import build_inline_worker
from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.knowledge.generation_pipeline import GenerationPipeline
from agentic_graphrag.knowledge.ingest_tasks import IngestStatus, IngestTaskStore
from agentic_graphrag.knowledge.review.queue import ReviewType


def selected(svc, kb):
    return select_service(svc, tenant_id=kb.tenant_id, kb_id=kb.kb_id)


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_pending_review_restores_approved_facts_after_graph_restart(root_service, decision):
    svc = root_service
    kb = knowledge_store(svc).create(KnowledgeBase(kb_id="restart", review_policy="required"))
    shadow, row = upload(
        svc,
        kb,
        text=(
            "Alice Stone is the CEO of Apex Holdings. Bob Jones is the CEO of NovaTech Industries."
        ),
    )
    build_inline_worker(svc).run_once()
    items = shadow.review_queue.list()
    assert len(items) == 2
    ReviewApp(selected(svc, kb)).decide(items[0].id, "approve", tenant_id=kb.tenant_id)
    assert shadow.bundle.graph.counts()["relations"] == 1
    svc.bundle.graph.clear()
    ReviewApp(selected(svc, kb)).decide(items[1].id, decision, tenant_id=kb.tenant_id)
    active = selected(svc, kb)
    assert active.ingest_tasks.get("upload").status == "done"
    assert active.bundle.graph.counts()["relations"] == (2 if decision == "approve" else 1)
    assert active.bundle.docs.get(row["doc_id"]).metadata["index_complete"]


@pytest.mark.parametrize("recover", ["retry", "reconcile"])
def test_review_publication_failure_is_retryable_without_false_done(
    root_service, monkeypatch, recover
):
    svc = root_service
    kb = knowledge_store(svc).create(KnowledgeBase(kb_id="review", review_policy="required"))
    shadow, _ = upload(
        svc,
        kb,
        text=(
            "Alice Stone is the CEO of Apex Holdings. Bob Jones is the CEO of NovaTech Industries."
        ),
    )
    build_inline_worker(svc).run_once()
    first, item = shadow.review_queue.list()
    ReviewApp(selected(svc, kb)).decide(first.id, "approve", tenant_id=kb.tenant_id)
    complete = GenerationPipeline.complete

    def unavailable(*args, **kwargs):
        raise OSError("metadata temporarily unavailable")

    monkeypatch.setattr(GenerationPipeline, "complete", unavailable)
    with pytest.raises(OSError):
        ReviewApp(selected(svc, kb)).decide(item.id, "approve", tenant_id=kb.tenant_id)
    assert shadow.ingest_tasks.get("upload").status == "review"
    assert selected(svc, kb).bundle.graph.counts()["relations"] == 0
    monkeypatch.setattr(GenerationPipeline, "complete", complete)
    svc.bundle.graph.clear()
    if recover == "retry":
        ReviewApp(selected(svc, kb)).retry(item.id, tenant_id=kb.tenant_id)
    else:
        from agentic_graphrag.app.knowledge_maintenance import maintain_knowledge

        result = maintain_knowledge(svc, tenant_id=kb.tenant_id, kb_id=kb.kb_id, action="reconcile")
        assert result["status"] == "done"
    assert shadow.ingest_tasks.get("upload").status == "done"
    active = selected(svc, kb)
    assert active.bundle.graph.counts()["relations"] == 2
    assert active.knowledge_base.active_generation == shadow.knowledge_base.active_generation


def test_interrupted_approval_replay_recovers_again_without_new_decisions(
    root_service, monkeypatch
):
    from agentic_graphrag.knowledge.review.executor import ReviewExecutor

    svc = root_service
    kb = knowledge_store(svc).create(KnowledgeBase(kb_id="interrupted", review_policy="required"))
    shadow, _ = upload(
        svc,
        kb,
        text=(
            "Alice Stone is the CEO of Apex Holdings. Bob Jones is the CEO of NovaTech Industries."
        ),
    )
    build_inline_worker(svc).run_once()
    first, second = shadow.review_queue.list()
    approved = ReviewApp(selected(svc, kb)).decide(first.id, "approve", tenant_id=kb.tenant_id)
    receipt = dict(approved.execution)
    svc.bundle.graph.clear()
    write = ReviewExecutor._write

    def unavailable(*args, **kwargs):
        raise OSError("graph unavailable during recovery")

    monkeypatch.setattr(ReviewExecutor, "_write", unavailable)
    with pytest.raises(OSError):
        ReviewApp(selected(svc, kb)).decide(second.id, "approve", tenant_id=kb.tenant_id)
    svc.bundle.graph.clear()
    monkeypatch.setattr(ReviewExecutor, "_write", write)
    ReviewApp(selected(svc, kb)).retry(second.id, tenant_id=kb.tenant_id)
    assert selected(svc, kb).bundle.graph.counts()["relations"] == 2
    assert shadow.ingest_tasks.get("upload").status == "done"
    assert shadow.review_queue.get(first.id).execution == receipt


def test_published_spotcheck_rejection_creates_generation_preserving_history(root_service):
    svc = root_service
    kb = knowledge_store(svc).create(KnowledgeBase(kb_id="spotcheck"))
    _, row = upload(svc, kb)
    build_inline_worker(svc).run_once()
    active = selected(svc, kb)
    generation = active.knowledge_base.active_generation
    item = active.review_queue.enqueue(
        ReviewType.SPOTCHECK,
        {"doc_ids": [row["doc_id"]], "doc_versions": {row["doc_id"]: int(row["version"])}},
        tenant_id=kb.tenant_id,
    )
    ReviewApp(active).decide(item.id, "reject", tenant_id=kb.tenant_id)
    current = selected(svc, kb)
    assert current.knowledge_base.active_generation != generation
    assert current.bundle.graph.counts()["relations"] == 0
    historical = select_documents(active, None, tenant_id=kb.tenant_id)
    assert historical.bundle.graph.counts()["relations"] == 1


def test_worker_recovers_after_publication_before_done_without_writing_snapshot(
    root_service, monkeypatch
):
    svc = root_service
    kb = knowledge_store(svc).create(KnowledgeBase(kb_id="crash"))
    shadow, row = upload(svc, kb)
    transition = IngestTaskStore.transition

    def crash_before_done(self, task_id, status, **kwargs):
        if status == IngestStatus.DONE:
            raise SystemExit("crash after publication")
        return transition(self, task_id, status, **kwargs)

    monkeypatch.setattr(IngestTaskStore, "transition", crash_before_done)
    with pytest.raises(SystemExit):
        build_inline_worker(svc).run_once()
    assert knowledge_store(svc).get_write(kb.scope, "upload").status == "completed"
    monkeypatch.setattr(IngestTaskStore, "transition", transition)
    svc.ingest_tasks.requeue_stale(stale_seconds=0)
    build_inline_worker(svc).run_once()
    assert shadow.ingest_tasks.get("upload").status == "done"
    assert selected(svc, kb).bundle.docs.get(row["doc_id"]).metadata["index_complete"]


def test_old_spotcheck_cannot_withdraw_replacement_document_facts(root_service):
    svc = root_service
    kb = knowledge_store(svc).create(KnowledgeBase(kb_id="spotcheck"))
    _, row = upload(svc, kb)
    build_inline_worker(svc).run_once()
    active = selected(svc, kb)
    doc = active.bundle.docs.get(row["doc_id"])
    item = active.review_queue.enqueue(
        ReviewType.SPOTCHECK,
        {"doc_ids": [doc.doc_id], "doc_versions": {doc.doc_id: doc.version}},
        tenant_id=kb.tenant_id,
    )
    shadow = prepare_upload(active, "replacement")
    replacement = lifecycle_for(shadow).accept(
        DocumentRecord(
            "", doc.title, "Bob Jones is the CEO of Apex Holdings.", source_uri=doc.source_uri
        )
    )
    assert replacement["doc_id"] == doc.doc_id and replacement["version"] == "2"
    shadow.ingest_tasks.create(kb.tenant_id, [replacement], task_id="replacement")
    build_inline_worker(svc).run_once()
    reviewed = ReviewApp(selected(svc, kb)).decide(item.id, "reject", tenant_id=kb.tenant_id)
    assert reviewed.execution["effect"] == "stale_document"
    current = selected(svc, kb)
    assert current.bundle.graph.counts()["relations"] == 1
