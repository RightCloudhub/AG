"""Tenant and membership HTTP endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from agentic_graphrag.api.envelope import ok
from agentic_graphrag.webapp import tenants
from agentic_graphrag.webapp.identity import identity
from agentic_graphrag.webapp.routes_auth import grant_response
from agentic_graphrag.webapp.runtime import principal, runtime, user_principal
from agentic_graphrag.webapp.schemas_tenants import MemberInput, TenantInput
from agentic_graphrag.webapp.tenant_access import clean_name, tenant_data, tenant_scope

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.get("")
def list_tenants(request: Request) -> dict[str, Any]:
    return ok(identity(runtime(request).database, principal(request))["tenants"])


@router.post("", status_code=201)
def create(request: Request, body: TenantInput) -> dict[str, Any]:
    return ok(tenants.create_tenant(runtime(request).database, user_principal(request), body.name))


@router.get("/{tenant_id}")
def detail(request: Request, tenant_id: str) -> dict[str, Any]:
    with runtime(request).database.transaction() as session:
        tenant = tenant_scope(session, principal(request), tenant_id)
        return ok(tenant_data(session, tenant))


@router.patch("/{tenant_id}")
def rename(request: Request, tenant_id: str, body: TenantInput) -> dict[str, Any]:
    with runtime(request).database.transaction(write=True) as session:
        tenant = tenant_scope(session, principal(request), tenant_id, admin=True, lock=True)
        tenant.name = clean_name(body.name)
        return ok(tenant_data(session, tenant))


@router.post("/{tenant_id}/switch")
def switch(request: Request, response: Response, tenant_id: str) -> dict[str, Any]:
    ctx = runtime(request)
    grant = tenants.switch_tenant(ctx.database, user_principal(request), tenant_id)
    return grant_response(ctx, response, grant)


@router.get("/{tenant_id}/members")
def members(request: Request, tenant_id: str) -> dict[str, Any]:
    return ok(tenants.list_members(runtime(request).database, principal(request), tenant_id))


@router.patch("/{tenant_id}/members/{user_id}")
def edit_member(
    request: Request, body: MemberInput, *, tenant_id: str, user_id: str
) -> dict[str, Any]:
    return ok(
        tenants.change_member(
            runtime(request).database,
            principal(request),
            tenant_id=tenant_id,
            user_id=user_id,
            role=body.role.value,
        )
    )


@router.delete("/{tenant_id}/members/{user_id}")
def remove_member(request: Request, tenant_id: str, user_id: str) -> dict[str, Any]:
    return ok(
        tenants.change_member(
            runtime(request).database,
            principal(request),
            tenant_id=tenant_id,
            user_id=user_id,
            role=None,
        )
    )
