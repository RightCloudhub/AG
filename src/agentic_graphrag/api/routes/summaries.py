"""Summary readiness and recoverable jobs, separate from base document readiness."""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.api.envelope import MetaBody, TypedEnvelope, ok
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.deps import principal, service
from agentic_graphrag.app.errors import INVALID_INPUT, ApiError
from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.app.summary_prepare import require_summary_capability
from agentic_graphrag.app.summary_queue import (
    SummaryIndexStatus,
    queue_summary_index,
    summary_status,
)
from agentic_graphrag.config import runtime_dir
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.summary_jobs import SummaryJob
from agentic_graphrag.knowledge.generation_pipeline import GenerationPipeline
from agentic_graphrag.stores.summary_jobs import SummaryJobStore

router = APIRouter(prefix="/v1/knowledge-bases", tags=["summaries"])
_manage = [Depends(require_role(Role.ADMIN, Role.OPERATOR))]


class SummaryBuild(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rebuild: bool = False
    operation_id: str | None = Field(default=None, min_length=1, max_length=128)


@router.get(
    "/{kb_id}/summary-index",
    response_model=TypedEnvelope[SummaryIndexStatus],
    operation_id="get_summary_index",
)
def get_summary_index(kb_id: str, request: Request) -> JsonObject:
    tenant, _ = principal(request)
    selected = select_service(service(request), tenant_id=tenant, kb_id=kb_id)
    return ok(summary_status(selected).model_dump(mode="json"))


@router.post(
    "/{kb_id}/summary-index/build",
    response_model=TypedEnvelope[SummaryIndexStatus],
    operation_id="build_summary_index",
    dependencies=_manage,
)
def build_index(kb_id: str, body: SummaryBuild, request: Request) -> JsonObject:
    tenant, _ = principal(request)
    root = service(request)
    selected = select_service(root, tenant_id=tenant, kb_id=kb_id)
    require_summary_capability(selected)
    try:
        if body.rebuild:
            if not body.operation_id:
                raise ValueError("An operation_id is required for a rebuild")
            kb = selected.knowledge_base
            assert kb is not None
            pipeline = GenerationPipeline(root.bundle, knowledge_store(root))
            write = pipeline.prepare(kb.scope, operation_id=body.operation_id)
            pipeline.complete(write)
            selected = select_service(root, tenant_id=tenant, kb_id=kb_id)
        result = queue_summary_index(selected)
    except ValueError as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=409) from exc
    return ok(result.model_dump(mode="json"))


@router.get(
    "/{kb_id}/summary-jobs",
    response_model=TypedEnvelope[list[SummaryJob]],
    operation_id="list_summary_jobs",
    dependencies=_manage,
)
def list_jobs(
    kb_id: str,
    request: Request,
    *,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Literal["pending", "running", "completed", "failed", "superseded"] | None = None,
) -> JsonObject:
    tenant, _ = principal(request)
    svc = select_service(service(request), tenant_id=tenant, kb_id=kb_id)
    kb = svc.knowledge_base
    assert kb is not None
    jobs = SummaryJobStore(runtime_dir(svc.cfg) / "knowledge.sqlite3")
    try:
        rows = jobs.jobs(kb.scope, generation=kb.active_generation, limit=100000)
        if status is not None:
            rows = [row for row in rows if row.status == status]
    finally:
        jobs.close()
    return ok(
        [
            row.model_dump(mode="json", exclude={"owner", "lease_token"})
            for row in rows[offset : offset + limit]
        ],
        meta=MetaBody(total=len(rows), limit=limit, page=offset // limit + 1),
    )


@router.post(
    "/{kb_id}/summary-jobs/{job_id}/retry",
    response_model=TypedEnvelope[SummaryJob],
    operation_id="retry_summary_job",
    dependencies=_manage,
)
def retry_job(kb_id: str, job_id: str, request: Request) -> JsonObject:
    tenant, _ = principal(request)
    svc = select_service(service(request), tenant_id=tenant, kb_id=kb_id)
    kb = svc.knowledge_base
    assert kb is not None
    jobs = SummaryJobStore(runtime_dir(svc.cfg) / "knowledge.sqlite3")
    try:
        job = jobs.get_job(kb.scope, job_id)
        if job is None:
            raise ApiError(INVALID_INPUT, "Unknown summary job", status_code=404)
        if job.generation != kb.active_generation:
            raise ValueError("Only the active generation can retry")
        result = jobs.retry(kb.scope, job_id)
    except ValueError as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=409) from exc
    finally:
        jobs.close()
    return ok(result.model_dump(mode="json", exclude={"owner", "lease_token"}))
