"""Versioned knowledge-base configuration within the authenticated tenant."""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.api.envelope import MetaBody, TypedEnvelope, ok
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.deps import principal, service
from agentic_graphrag.app.errors import INVALID_INPUT, ApiError
from agentic_graphrag.app.knowledge_config import pin_profile, update_configuration
from agentic_graphrag.app.knowledge_scope import knowledge_store, resolve_knowledge_base
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.knowledge import KnowledgeBase

router = APIRouter(prefix="/v1", tags=["knowledge-bases"])


class KnowledgeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kb_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    name: str = Field(min_length=1, max_length=200)
    build_mode: Literal["schema", "open"] = "schema"
    schema_id: str | None = None
    schema_version: str | None = None
    model_profile_id: str | None = None
    model_profile_revision: int | None = Field(default=None, ge=1)
    global_summary_enabled: bool = False
    review_policy: Literal["confidence", "required"] = "confidence"


class KnowledgeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    model_profile_id: str | None = None
    model_profile_revision: int | None = Field(default=None, ge=1)
    operation_id: str | None = Field(default=None, min_length=1, max_length=128)
    global_summary_enabled: bool | None = None
    review_policy: Literal["confidence", "required"] | None = None


@router.get(
    "/knowledge-bases",
    response_model=TypedEnvelope[list[KnowledgeBase]],
    operation_id="list_knowledge_bases",
)
def list_knowledge_bases(
    request: Request, *, page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=100)
) -> JsonObject:
    tenant, _ = principal(request)
    store = knowledge_store(service(request))
    store.ensure_default(tenant)
    all_rows = store.list(tenant, limit=100000)
    rows = all_rows[(page - 1) * limit : page * limit]
    return ok(
        [row.model_dump(mode="json") for row in rows],
        meta=MetaBody(total=len(all_rows), page=page, limit=limit),
    )


@router.post(
    "/knowledge-bases",
    response_model=TypedEnvelope[KnowledgeBase],
    operation_id="create_knowledge_base",
    dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))],
)
def create_knowledge_base(body: KnowledgeCreate, request: Request) -> JsonObject:
    tenant, _ = principal(request)
    if body.build_mode == "open" and body.schema_id is not None:
        raise ApiError(INVALID_INPUT, "Open mode cannot bind a schema", status_code=422)
    kb = KnowledgeBase(
        tenant_id=tenant,
        **body.model_dump(),
        rule_planner_profile="legacy-company"
        if body.build_mode == "schema" and body.schema_id is None
        else None,
    )
    try:
        kb = pin_profile(service(request), kb)
        saved = knowledge_store(service(request)).create(kb)
    except (KeyError, ValueError) as exc:
        raise ApiError(
            INVALID_INPUT, "Knowledge base or published model profile is invalid", status_code=409
        ) from exc
    return ok(saved.model_dump(mode="json"))


@router.get(
    "/knowledge-bases/{kb_id}",
    response_model=TypedEnvelope[KnowledgeBase],
    operation_id="get_knowledge_base",
)
def get_knowledge_base(kb_id: str, request: Request) -> JsonObject:
    tenant, _ = principal(request)
    return ok(resolve_knowledge_base(service(request), tenant, kb_id).model_dump(mode="json"))


@router.patch(
    "/knowledge-bases/{kb_id}",
    response_model=TypedEnvelope[KnowledgeBase],
    operation_id="update_knowledge_base",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def update_knowledge_base(kb_id: str, body: KnowledgeUpdate, request: Request) -> JsonObject:
    tenant, _ = principal(request)
    svc = service(request)
    current = resolve_knowledge_base(svc, tenant, kb_id)
    changes = body.model_dump(exclude_unset=True, exclude={"expected_revision", "operation_id"})
    if "model_profile_id" in changes and "model_profile_revision" not in changes:
        changes["model_profile_revision"] = None
    try:
        replacement = KnowledgeBase.model_validate({**current.model_dump(), **changes})
        saved = update_configuration(
            svc,
            replacement,
            expected_revision=body.expected_revision,
            operation_id=body.operation_id,
        )
    except (KeyError, ValueError) as exc:
        raise ApiError(
            INVALID_INPUT, "Invalid or stale configuration revision", status_code=409
        ) from exc
    return ok(saved.model_dump(mode="json"))
