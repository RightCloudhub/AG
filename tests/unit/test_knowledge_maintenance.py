"""CLI maintenance obeys scope, publication and review recovery boundaries."""

import pytest
from test_knowledge_workflow import root_service as root_service
from test_knowledge_workflow import upload

from agentic_graphrag.app.knowledge_maintenance import maintain_knowledge
from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.app.write_path import build_inline_worker
from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.knowledge.ingest_worker import IngestResult


def maintain(svc, action, *, kb_id="one", operation="maintenance"):
    return maintain_knowledge(
        svc, tenant_id="default", kb_id=kb_id, action=action, operation_id=operation
    )


def test_reconcile_reports_required_review_instead_of_done(root_service):
    kb = knowledge_store(root_service).create(KnowledgeBase(kb_id="one", review_policy="required"))
    upload(root_service, kb)
    result = maintain(root_service, "reconcile")
    assert result["status"] == "review"
    assert knowledge_store(root_service).get(kb.scope).active_generation == "1"


def test_reconcile_repairs_untracked_legacy_documents_in_selected_knowledge_base(root_service):
    for key in ("one", "two"):
        knowledge_store(root_service).create(KnowledgeBase(kb_id=key))
        selected = select_service(root_service, tenant_id="default", kb_id=key)
        selected.bundle.docs.save(
            DocumentRecord("legacy", "source", "Alice Stone is the CEO of Apex Holdings.")
        )
    result = maintain(root_service, "reconcile")
    assert result["status"] == "done"
    selected = select_service(root_service, tenant_id="default", kb_id="one")
    assert selected.bundle.docs.get("legacy").metadata["index_complete"]
    assert selected.knowledge_base.active_generation != "1"
    other = select_service(root_service, tenant_id="default", kb_id="two")
    assert not other.bundle.docs.get("legacy").metadata.get("index_complete")
    assert other.bundle.graph.counts()["relations"] == 0


def test_migration_replay_preserves_a_batch_waiting_for_review(root_service):
    knowledge_store(root_service).create(KnowledgeBase(kb_id="one", review_policy="required"))
    selected = select_service(root_service, tenant_id="default", kb_id="one")
    selected.bundle.docs.save(
        DocumentRecord("legacy", "source", "Alice Stone is the CEO of Apex Holdings.")
    )
    first = maintain(root_service, "migrate-doc-ids")
    assert first["status"] == "review"
    original_task = selected.ingest_tasks.get("maintenance")
    second = maintain(root_service, "migrate-doc-ids")
    assert second["status"] == "review"
    replayed = selected.ingest_tasks.get("maintenance")
    assert replayed.created_at == original_task.created_at
    assert replayed.docs == original_task.docs


def test_failed_reindex_keeps_active_generation_and_replay_publishes_once(
    root_service, monkeypatch
):
    kb = knowledge_store(root_service).create(KnowledgeBase(kb_id="one"))
    upload(root_service, kb)
    build_inline_worker(root_service).run_once()
    previous = knowledge_store(root_service).get(kb.scope).active_generation
    from agentic_graphrag.knowledge.ingest_worker import IngestWorker

    monkeypatch.setattr(IngestWorker, "reindex", lambda *a, **k: [IngestResult("doc", error="bad")])
    with pytest.raises(ValueError, match="old generation"):
        maintain(root_service, "reindex")
    assert knowledge_store(root_service).get(kb.scope).active_generation == previous
    monkeypatch.setattr(IngestWorker, "reindex", lambda *a, **k: [])
    assert maintain(root_service, "reindex")["status"] == "done"
    current = knowledge_store(root_service).get(kb.scope)
    assert current.active_generation != previous
    assert maintain(root_service, "reindex")["generation"] == current.active_generation
    assert knowledge_store(root_service).get(kb.scope) == current


def test_reused_task_id_cannot_replace_another_scopes_work(root_service):
    services = []
    for key in ("one", "two"):
        knowledge_store(root_service).create(KnowledgeBase(kb_id=key))
        services.append(select_service(root_service, tenant_id="default", kb_id=key))
    services[0].ingest_tasks.create("default", [{"doc_id": "d"}], task_id="shared-id")
    with pytest.raises(ValueError, match="already exists"):
        services[1].ingest_tasks.create("default", [{"doc_id": "d"}], task_id="shared-id")
    assert services[0].ingest_tasks.get("shared-id") is not None
    assert services[1].ingest_tasks.get("shared-id") is None
