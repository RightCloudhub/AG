"""Admin-only model catalogue, drafts, capability tests and explicit activation."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.api.envelope import ok
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.deps import principal, service
from agentic_graphrag.app.errors import INVALID_INPUT, ApiError
from agentic_graphrag.app.model_profiles import ModelProfiles
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.models import ModelProfile, ModelRole
from agentic_graphrag.llm.profile_store import ProfileConflict

router = APIRouter(
    prefix="/v1/model-profiles",
    tags=["models"],
    dependencies=[Depends(require_role(Role.ADMIN))],
)


class ProfileDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=120)
    roles: dict[ModelRole, str | None]
    expected_revision: int = Field(default=0, ge=0)


class ProfileRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


class ProfilePublication(ProfileRevision):
    expected_active_revision: int = Field(ge=0)


def _profiles(request: Request) -> ModelProfiles:
    svc = service(request)
    return ModelProfiles(svc.cfg, svc.settings)


def _checked[T](action: Callable[[], T]) -> T:
    try:
        return action()
    except KeyError as exc:
        raise ApiError(INVALID_INPUT, "Model profile not found", status_code=404) from exc
    except ProfileConflict as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=409) from exc
    except ValueError as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=422) from exc


@router.get("/catalog", operation_id="listModelCatalog")
def list_catalog(request: Request) -> JsonObject:
    tenant_id, _ = principal(request)
    return ok(_profiles(request).catalog(tenant_id=tenant_id))


@router.get("", operation_id="listModelProfiles")
def list_profiles(request: Request) -> JsonObject:
    tenant_id, _ = principal(request)
    profiles = _profiles(request)
    rows = []
    for profile in profiles.store.list(tenant_id=tenant_id):
        row = profile.model_dump(mode="json")
        row["active_revision"] = profiles.store.active_revision(profile.id, tenant_id=tenant_id)
        result = profiles.store.test_result(
            profile.id, tenant_id=tenant_id, revision=profile.revision
        )
        row["test"] = result.model_dump(mode="json") if result else None
        rows.append(row)
    return ok(rows)


@router.post("", operation_id="saveModelProfileDraft")
def save_profile(body: ProfileDraft, request: Request) -> JsonObject:
    tenant_id, _ = principal(request)
    profile = ModelProfile(id=body.id, tenant_id=tenant_id, name=body.name, roles=body.roles)
    saved = _checked(
        lambda: _profiles(request).save_draft(profile, expected_revision=body.expected_revision)
    )
    return ok(saved.model_dump(mode="json"))


@router.get("/{profile_id}", operation_id="getModelProfile")
def get_profile(profile_id: str, request: Request) -> JsonObject:
    tenant_id, _ = principal(request)
    profile = _checked(lambda: _profiles(request).store.get(profile_id, tenant_id=tenant_id))
    return ok(profile.model_dump(mode="json"))


@router.post("/{profile_id}/test", operation_id="testModelProfile")
def run_test(profile_id: str, body: ProfileRevision, request: Request) -> JsonObject:
    tenant_id, _ = principal(request)
    result = _checked(
        lambda: _profiles(request).test(profile_id, tenant_id=tenant_id, revision=body.revision)
    )
    return ok({**result.model_dump(mode="json"), "passed": result.passed})


@router.post("/{profile_id}/publish", operation_id="publishModelProfile")
def publish_profile(profile_id: str, body: ProfilePublication, request: Request) -> JsonObject:
    tenant_id, user_id = principal(request)
    result = _checked(
        lambda: _profiles(request).publish(
            profile_id,
            tenant_id=tenant_id,
            revision=body.revision,
            expected_active_revision=body.expected_active_revision,
            actor=user_id,
        )
    )
    return ok(result.model_dump(mode="json"))


@router.post("/{profile_id}/rollback", operation_id="rollbackModelProfile")
def rollback_profile(profile_id: str, body: ProfilePublication, request: Request) -> JsonObject:
    return publish_profile(profile_id, body, request)
