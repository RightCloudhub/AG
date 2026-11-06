"""Reviewable Schema proposals, publication and rollback within one knowledge base."""

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.api.envelope import MetaBody, TypedEnvelope, ok
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.deps import principal, service
from agentic_graphrag.api.routes.knowledge_upload import emit_audit
from agentic_graphrag.app.errors import INVALID_INPUT, ApiError
from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.app.schema_management import (
    approve_schema,
    create_draft,
    promote_schema,
    registry_for,
)
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.core.schema_drafts import DraftApproval, SchemaDraft

router = APIRouter(
    prefix="/v1/knowledge-bases",
    tags=["schemas"],
    dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))],
)


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aliases: dict[str, str] = Field(default_factory=dict, max_length=500)


class PromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=128)


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    expected_revision: int = Field(ge=1)


@router.post(
    "/{kb_id}/schema-drafts",
    response_model=TypedEnvelope[SchemaDraft],
    operation_id="create_schema_draft",
)
def post_draft(kb_id: str, body: DraftRequest, request: Request) -> JsonObject:
    tenant, _ = principal(request)
    svc = select_service(service(request), tenant_id=tenant, kb_id=kb_id)
    try:
        draft = create_draft(svc, aliases=body.aliases)
    except ValueError as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=409) from exc
    return ok(draft.model_dump(mode="json", by_alias=True))


@router.get(
    "/{kb_id}/schema-drafts",
    response_model=TypedEnvelope[list[SchemaDraft]],
    operation_id="list_schema_drafts",
)
def list_drafts(
    kb_id: str,
    request: Request,
    *,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> JsonObject:
    tenant, _ = principal(request)
    svc = select_service(service(request), tenant_id=tenant, kb_id=kb_id)
    assert svc.knowledge_base is not None
    with registry_for(svc) as registry:
        rows = registry.list_drafts(svc.knowledge_base.scope)
    return ok(
        [row.model_dump(mode="json", by_alias=True) for row in rows[offset : offset + limit]],
        meta=MetaBody(total=len(rows), limit=limit, page=offset // limit + 1),
    )


@router.get(
    "/{kb_id}/schema-drafts/{draft_id}",
    response_model=TypedEnvelope[SchemaDraft],
    operation_id="get_schema_draft",
)
def get_draft(kb_id: str, draft_id: str, request: Request) -> JsonObject:
    tenant, _ = principal(request)
    svc = select_service(service(request), tenant_id=tenant, kb_id=kb_id)
    assert svc.knowledge_base is not None
    with registry_for(svc) as registry:
        draft = registry.get_draft(svc.knowledge_base.scope, draft_id)
    if draft is None:
        raise ApiError(INVALID_INPUT, "Unknown Schema draft", status_code=404)
    return ok(draft.model_dump(mode="json", by_alias=True))


@router.post(
    "/{kb_id}/schema-drafts/{draft_id}/approve",
    response_model=TypedEnvelope[SchemaDraft],
    operation_id="approve_schema_draft",
)
def approve(kb_id: str, draft_id: str, *, body: DraftApproval, request: Request) -> JsonObject:
    tenant, actor = principal(request)
    svc = select_service(service(request), tenant_id=tenant, kb_id=kb_id)
    try:
        result = approve_schema(svc, draft_id, approval=body.model_copy(update={"reviewer": actor}))
    except KeyError as exc:
        raise ApiError(INVALID_INPUT, "Unknown Schema draft", status_code=404) from exc
    except ValueError as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=409) from exc
    emit_audit(request, {"action": "schema_approve", "target": draft_id, "outcome": "approved"})
    return ok(result.model_dump(mode="json", by_alias=True))


@router.post(
    "/{kb_id}/schema-drafts/{draft_id}/promote",
    response_model=TypedEnvelope[KnowledgeBase],
    operation_id="promote_schema_draft",
)
def promote(kb_id: str, draft_id: str, *, body: PromotionRequest, request: Request) -> JsonObject:
    tenant, _ = principal(request)
    svc = select_service(service(request), tenant_id=tenant, kb_id=kb_id)
    try:
        result = promote_schema(svc, draft_id, operation_id=body.operation_id)
    except ValueError as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=409) from exc
    emit_audit(request, {"action": "schema_promote", "target": draft_id, "outcome": "published"})
    return ok(result.model_dump(mode="json"))


@router.post(
    "/{kb_id}/rollback",
    response_model=TypedEnvelope[KnowledgeBase],
    operation_id="rollback_knowledge_generation",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def rollback(kb_id: str, body: RollbackRequest, request: Request) -> JsonObject:
    tenant, _ = principal(request)
    root = service(request)
    selected = select_service(root, tenant_id=tenant, kb_id=kb_id)
    assert selected.knowledge_base is not None
    try:
        restored = knowledge_store(root).publish(
            selected.knowledge_base.scope,
            generation=body.generation,
            expected_revision=body.expected_revision,
        )
    except ValueError as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=409) from exc
    if root.retrieval_cache is not None:
        root.retrieval_cache.on_index_update()
    emit_audit(
        request, {"action": "knowledge_rollback", "target": kb_id, "outcome": body.generation}
    )
    return ok(restored.model_dump(mode="json"))
