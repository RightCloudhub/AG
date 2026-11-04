"""Scoped repair and migration share upload publication and recoverable task journals."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.app.knowledge_writes import prepare_upload, publish_task
from agentic_graphrag.app.write_path import build_inline_worker
from agentic_graphrag.knowledge.index_result import IngestResult
from agentic_graphrag.knowledge.ingest_tasks import IngestStatus, IngestTask

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService
    from agentic_graphrag.knowledge.ingest_worker import IngestWorker


def maintain_knowledge(
    root: QueryService, *, tenant_id: str, kb_id: str, action: str, operation_id: str | None = None
) -> dict[str, Any]:
    if action not in {"reconcile", "reindex", "migrate-doc-ids"}:
        raise ValueError("Unknown maintenance action")
    selected = select_service(root, tenant_id=tenant_id, kb_id=kb_id)
    if action == "reconcile":
        return _reconcile(root, selected, operation=operation_id or f"repair-{uuid4()}")
    operation = operation_id or f"{action}-{uuid4()}"
    selected = prepare_upload(selected, operation)
    kb = selected.knowledge_base
    assert kb is not None
    intent = knowledge_store(root).get_write(kb.scope, operation)
    if intent is not None and intent.status == "completed":
        return {"operation_id": operation, "generation": intent.target_generation, "status": "done"}
    worker = build_inline_worker(selected, route_tasks=False)
    try:
        if action == "reindex":
            return _reindex(worker, selected, operation=operation)
        return _migrate(worker, selected, operation=operation)
    finally:
        close_worker_client(worker)


def _reindex(worker: IngestWorker, selected: QueryService, *, operation: str) -> dict[str, Any]:
    assert selected.knowledge_base is not None
    results = worker.reindex(tenant_id=selected.knowledge_base.tenant_id)
    if any(result.error for result in results):
        raise ValueError("Reindex failed; the old generation remains active")
    publish_task(selected, operation)
    return {"operation_id": operation, "status": "done", "results": [asdict(r) for r in results]}


def close_worker_client(worker: IngestWorker) -> None:
    llm = worker.graph_ingestor.llm if worker.graph_ingestor else None
    if (close := getattr(llm, "close", None)) is not None:
        close()


def _migrate(worker: IngestWorker, selected: QueryService, *, operation: str) -> dict[str, Any]:
    kb = selected.knowledge_base
    assert kb is not None and selected.ingest_tasks is not None
    task = selected.ingest_tasks.get(operation)
    if task is not None:
        _run_pending(worker, selected, task)
        task = selected.ingest_tasks.get(operation)
        assert task is not None
        return {"operation_id": operation, "status": task.status, "documents": task.docs}
    rows = worker.pipeline.lifecycle.migrate(tenant_id=kb.tenant_id)
    selected.ingest_tasks.remap_documents(
        {row["old_id"]: row["doc_id"] for row in rows}, tenant_id=kb.tenant_id
    )
    documents = [{"doc_id": row["doc_id"], "status": "created"} for row in rows if row["doc_id"]]
    if not documents:
        publish_task(selected, operation)
        return {"operation_id": operation, "status": "done", "migrations": rows}
    task = selected.ingest_tasks.create(kb.tenant_id, documents, task_id=operation)
    _run_pending(worker, selected, task)
    task = selected.ingest_tasks.get(task.id)
    assert task is not None
    return {"operation_id": operation, "status": task.status, "migrations": rows}


def _reconcile(root: QueryService, selected: QueryService, *, operation: str) -> dict[str, Any]:
    kb = selected.knowledge_base
    assert kb is not None and selected.ingest_tasks is not None and root.ingest_tasks is not None
    worker = build_inline_worker(root)
    try:
        selected.ingest_tasks.requeue_stale(stale_seconds=worker.config.stale_task_seconds)
        results = [r for task in _tasks(selected) for r in _run_pending(worker, selected, task)]
    finally:
        close_worker_client(worker)
    selected = select_service(root, tenant_id=kb.tenant_id, kb_id=kb.kb_id)
    results.extend(_repair_untracked(selected, operation=operation))
    tasks = _tasks(selected)
    return {
        "status": _status(tasks, results),
        "tasks": {task.id: task.status for task in tasks},
        "results": [asdict(r) for r in results],
    }


def _tasks(svc: QueryService) -> list[IngestTask]:
    assert svc.ingest_tasks is not None
    _, total = svc.ingest_tasks.list(limit=1)
    return svc.ingest_tasks.list(limit=max(1, total))[0]


def _run_pending(worker: IngestWorker, svc: QueryService, task: IngestTask) -> list[IngestResult]:
    assert svc.ingest_tasks is not None and worker.task_store is not None
    if task.status == IngestStatus.REVIEW:
        _recover_review(svc, task)
        return []
    if task.status in {IngestStatus.PARTIAL, IngestStatus.FAILED}:
        svc.ingest_tasks.transition(task.id, IngestStatus.QUEUED)
    current = worker.task_store.get(task.id)
    if current is not None and current.status == IngestStatus.QUEUED and worker._claim(task.id):
        return worker._run_task(current)
    return []


def _recover_review(svc: QueryService, task: IngestTask) -> None:
    from agentic_graphrag.app.review_app import ReviewApp

    shadow = prepare_upload(svc, task.id)
    queue = shadow.review_queue
    if queue is None:
        return
    _, total = queue.list_page(status=None, tenant_id=task.tenant_id, limit=1)
    rows = queue.list(status=None, tenant_id=task.tenant_id, limit=max(1, total))
    app = ReviewApp(shadow)
    for item in rows:
        if item.batch_id == task.id and item.status != "pending":
            app.retry(item.id, tenant_id=task.tenant_id)
            assert shadow.ingest_tasks is not None
            settled = shadow.ingest_tasks.get(task.id, tenant_id=task.tenant_id)
            if settled is not None and settled.status == IngestStatus.DONE:
                break


def _repair_untracked(svc: QueryService, *, operation: str) -> list[IngestResult]:
    kb = svc.knowledge_base
    assert kb is not None
    excluded = {
        row["doc_id"]
        for task in _tasks(svc)
        if task.status not in {IngestStatus.DONE, IngestStatus.EMPTY}
        for row in task.docs
    }
    probe = build_inline_worker(svc, route_tasks=False)
    try:
        rows = _repair_candidates(svc, probe, excluded=excluded)
    finally:
        close_worker_client(probe)
    if not rows:
        return []
    shadow = prepare_upload(svc, operation)
    assert shadow.ingest_tasks is not None
    task = shadow.ingest_tasks.get(operation) or shadow.ingest_tasks.create(
        kb.tenant_id, rows, task_id=operation
    )
    worker = build_inline_worker(shadow, route_tasks=False)
    try:
        return _run_pending(worker, shadow, task)
    finally:
        close_worker_client(worker)


def _repair_candidates(
    svc: QueryService, worker: IngestWorker, *, excluded: set[str]
) -> list[dict[str, str]]:
    kb = svc.knowledge_base
    assert kb is not None
    return [
        {"doc_id": did, "version": str(doc.version), "status": "queued"}
        for did in svc.bundle.docs.list_ids(tenant_id=kb.tenant_id)
        if did not in excluded
        and (doc := svc.bundle.docs.get(did, tenant_id=kb.tenant_id)) is not None
        and worker.pipeline.needs_processing(doc)
    ]


def _status(tasks: list[IngestTask], results: list[IngestResult]) -> str:
    statuses = {task.status for task in tasks}
    if any(r.error for r in results) or statuses & {IngestStatus.FAILED, IngestStatus.PARTIAL}:
        return "failed"
    if IngestStatus.REVIEW in statuses:
        return "review"
    return "pending" if statuses & {IngestStatus.EXTRACTING, IngestStatus.QUEUED} else "done"
