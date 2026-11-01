"""Web identity projection; never serializes credential models."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from agentic_graphrag.api.auth import Principal
from agentic_graphrag.webapp.database import Database
from agentic_graphrag.webapp.models import Membership, Tenant, User


def tenant_memberships(db: Database, user_id: str) -> list[dict[str, Any]]:
    with db.transaction() as session:
        rows = session.execute(
            select(Tenant, Membership)
            .join(
                Membership,
                Membership.tenant_id == Tenant.id,
            )
            .where(Membership.user_id == user_id)
            .order_by(Tenant.created_at, Tenant.id)
        ).all()
        return [
            {"id": tenant.id, "name": tenant.name, "kind": tenant.kind, "role": member.role}
            for tenant, member in rows
        ]


def identity(db: Database, principal: Principal) -> dict[str, Any]:
    admin = principal.role == "admin"
    data: dict[str, Any] = {
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        "role": principal.role.value,
        "authenticated": True,
        "email": None,
        "display_name": None,
        "capabilities": {
            "chat": True,
            "graph": True,
            "knowledge": principal.role in {"admin", "operator"},
            "review": principal.role in {"admin", "operator"},
            "ops": admin,
            "settings_profile": bool(principal.session_id),
            "settings_security": bool(principal.session_id),
            "settings_members": admin,
            "settings_api_keys": admin,
            "settings_tenant": admin,
        },
    }
    with db.transaction() as session:
        user = session.get(User, principal.user_id) if principal.session_id else None
        tenant = session.get(Tenant, principal.tenant_id)
        if user:
            data.update(email=user.email, display_name=user.display_name)
        data["tenants"] = [
            {
                "id": principal.tenant_id,
                "name": tenant.name if tenant else principal.tenant_id,
                "role": principal.role.value,
                "kind": tenant.kind if tenant else "team",
            }
        ]
    if principal.session_id:
        data["tenants"] = tenant_memberships(db, principal.user_id)
    return data
