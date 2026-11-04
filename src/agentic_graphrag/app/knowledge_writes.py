"""Bind upload tasks and worker recovery to a durable shadow generation."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.app.review_recovery import restore_required_reviews
from agentic_graphrag.knowledge.generation_pipeline import GenerationPipeline
from agentic_graphrag.knowledge.index_result import IngestResult
from agentic_graphrag.knowledge.ingest_tasks import IngestStatus, IngestTask
from agentic_graphrag.stores.scope_codec import parse_storage_tenant

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService
    from agentic_graphrag.knowledge.ingest_worker import IngestWorker


def prepare_upload(svc: QueryService, operation_id: str) -> QueryService:
    kb = svc.knowledge_base
    if kb is None or _legacy_write(svc):
        return svc
    root = svc.parent or svc
    pipeline = GenerationPipeline(root.bundle, knowledge_store(root))
    write = pipeline.prepare(kb.scope, operation_id=operation_id)
    selected = select_service(
        root, tenant_id=kb.tenant_id, kb_id=kb.kb_id, generation=write.target_generation
    )
    selected.bundle = write.bundle
    restore_required_reviews(selected, batch_id=operation_id)
    return selected


def _legacy_write(svc: QueryService) -> bool:
    kb = svc.knowledge_base
    return bool(
        kb
        and kb.kb_id == "default"
        and kb.build_mode == "schema"
        and kb.config_revision == 1
        and not kb.global_summary_enabled
        and kb.review_policy == "confidence"
        and kb.model_profile_id is None
    )


def run_scoped_task(root: QueryService, task: IngestTask, owner: str) -> list[IngestResult] | None:
    from agentic_graphrag.app.write_path import build_inline_worker

    scope, generation = parse_storage_tenant(task.tenant_id)
    generation = next((row["generation"] for row in task.docs if row.get("generation")), generation)
    svc = select_service(root, tenant_id=scope.tenant_id, kb_id=scope.kb_id, generation=generation)
    if generation == "1" and _legacy_write(svc) and not svc.allow_llm:
        return None
    metadata = knowledge_store(root)
    intent = metadata.get_write(scope, task.id)
    if intent is not None and intent.status == "completed":
        assert svc.ingest_tasks is not None
        svc.ingest_tasks.transition(
            task.id, IngestStatus.DONE, owner=owner, message="Recovered published generation"
        )
        return []
    if intent is not None:
        write = GenerationPipeline(root.bundle, metadata).prepare(scope, operation_id=task.id)
        svc.bundle = write.bundle
        restore_required_reviews(svc, batch_id=task.id)
    worker = build_inline_worker(svc, route_tasks=False, worker_id=owner)
    return _process(worker, replace(task, tenant_id=scope.tenant_id))


def _process(worker: IngestWorker, task: IngestTask) -> list[IngestResult]:
    try:
        return worker._run_task(task)
    finally:
        client = worker.graph_ingestor.llm if worker.graph_ingestor else None
        if (close := getattr(client, "close", None)) is not None:
            close()


def publish_task(svc: QueryService, task_id: str) -> None:
    from agentic_graphrag.app.entity_generations import carry_entities

    kb = svc.knowledge_base
    if kb is None:
        return
    root = svc.parent or svc
    metadata = knowledge_store(root)
    intent = metadata.get_write(kb.scope, task_id)
    if intent is None:
        return
    pipeline = GenerationPipeline(root.bundle, metadata)
    write = pipeline.prepare(kb.scope, operation_id=task_id)
    restore_required_reviews(replace(svc, bundle=write.bundle), batch_id=task_id)
    carry_entities(replace(svc, bundle=write.bundle))
    pipeline.complete(write)
    if root.retrieval_cache:
        root.retrieval_cache.on_index_update()
