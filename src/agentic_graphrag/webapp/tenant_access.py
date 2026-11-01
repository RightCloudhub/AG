"""Transactional tenant authorization and safe API projections."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentic_graphrag.api.auth import Principal
from agentic_graphrag.api.rbac import Role
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.webapp.models import Membership, Tenant
from agentic_graphrag.webapp.security import field_error


def not_found() -> ApiError:
    return ApiError("NOT_FOUND", "Resource not found", status_code=404)


def tenant_scope(
    session: Session, current: Principal, tenant_id: str, *, admin: bool = False, lock: bool = False
) -> Tenant:
    if tenant_id != current.tenant_id:
        raise not_found()
    tenant = session.get(Tenant, tenant_id, with_for_update=lock)
    if tenant is None:
        raise not_found()
    role = current.role.value
    if current.session_id:
        member = session.get(Membership, (current.user_id, tenant_id), with_for_update=lock)
        if member is None:
            raise not_found()
        role = member.role
    if admin and role != Role.ADMIN:
        raise ApiError("FORBIDDEN", "Tenant administrator required", status_code=403)
    return tenant


def clean_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise field_error("name", "invalid_name")
    return name


def tenant_data(session: Session, tenant: Tenant) -> dict[str, Any]:
    return {
        "id": tenant.id,
        "name": tenant.name,
        "kind": tenant.kind,
        "created_at": tenant.created_at,
        "member_count": session.scalar(
            select(func.count()).select_from(Membership).where(Membership.tenant_id == tenant.id)
        ),
    }


def actor_user(current: Principal) -> str | None:
    return current.user_id if current.session_id else None
