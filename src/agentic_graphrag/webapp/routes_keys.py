"""Tenant-fixed service API keys, stored as hashes and revealed once."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from agentic_graphrag.api.envelope import ok
from agentic_graphrag.webapp.models import ApiKey
from agentic_graphrag.webapp.runtime import principal, runtime
from agentic_graphrag.webapp.schemas_tenants import KeyInput
from agentic_graphrag.webapp.security import field_error, opaque_token, token_digest
from agentic_graphrag.webapp.tenant_access import actor_user, clean_name, not_found, tenant_scope

router = APIRouter(prefix="/tenants/{tenant_id}/api-keys", tags=["API keys"])
KEY_PREFIX_LENGTH = 12


def _data(key: ApiKey) -> dict[str, Any]:
    return {
        "id": key.id,
        "name": key.name,
        "prefix": key.prefix,
        "role": key.role,
        "created_at": key.created_at,
        "expires_at": key.expires_at,
        "last_used_at": key.last_used_at,
        "revoked_at": key.revoked_at,
    }


@router.get("")
def list_keys(request: Request, tenant_id: str) -> dict[str, Any]:
    with runtime(request).database.transaction() as session:
        tenant_scope(session, principal(request), tenant_id, admin=True)
        keys = session.scalars(
            select(ApiKey).where(ApiKey.tenant_id == tenant_id).order_by(ApiKey.created_at.desc())
        )
        return ok([_data(key) for key in keys])


@router.post("", status_code=201)
def create(request: Request, tenant_id: str, body: KeyInput) -> dict[str, Any]:
    current = principal(request)
    with runtime(request).database.transaction(write=True) as session:
        tenant_scope(session, current, tenant_id, admin=True, lock=True)
        if body.expires_at is not None and body.expires_at <= time.time():
            raise field_error("expires_at", "invalid_expiry")
        raw = f"agr_{opaque_token()}"
        key = ApiKey(
            tenant_id=tenant_id,
            name=clean_name(body.name),
            role=body.role.value,
            prefix=raw[:KEY_PREFIX_LENGTH],
            key_hash=token_digest(raw),
            expires_at=body.expires_at,
            created_by=actor_user(current),
        )
        session.add(key)
        session.flush()
        return ok({**_data(key), "key": raw})


@router.delete("/{key_id}")
def revoke(request: Request, tenant_id: str, key_id: str) -> dict[str, Any]:
    with runtime(request).database.transaction(write=True) as session:
        tenant_scope(session, principal(request), tenant_id, admin=True, lock=True)
        key = session.get(ApiKey, key_id, with_for_update=True)
        if key is None or key.tenant_id != tenant_id:
            raise not_found()
        key.revoked_at = time.time()
        return ok({"revoked": True})
