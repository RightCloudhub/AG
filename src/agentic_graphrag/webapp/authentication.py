"""Adapt Cookie sessions and service keys to the core's lightweight Principal."""

from __future__ import annotations

import time

from fastapi import Request
from sqlalchemy import select

from agentic_graphrag.api.auth import Principal, extract_api_key, user_id_for_api_key
from agentic_graphrag.api.rbac import Role, check_key_expiry, parse_api_keys_with_roles
from agentic_graphrag.webapp.cookies import ACCESS, decode_token, read_cookie
from agentic_graphrag.webapp.models import ApiKey, AuthSession
from agentic_graphrag.webapp.runtime import WebRuntime
from agentic_graphrag.webapp.security import token_digest, unauthorized
from agentic_graphrag.webapp.sessions import session_principal


def has_api_key(request: Request) -> bool:
    return "authorization" in request.headers or "x-api-key" in request.headers


def authenticate(ctx: WebRuntime, request: Request) -> Principal:
    if has_api_key(request):
        return _api_principal(ctx, extract_api_key(request) or "")
    claims = decode_token(ctx, read_cookie(ctx, request, ACCESS), audience="agr-access")
    with ctx.database.transaction() as session:
        record = session.get(AuthSession, claims.get("sid", ""))
        if record is None:
            raise unauthorized()
        if (record.user_id, record.tenant_id, record.version) != (
            claims.get("sub"),
            claims.get("tid"),
            claims.get("ver"),
        ):
            raise unauthorized()
        return session_principal(session, record)


def _api_principal(ctx: WebRuntime, key: str) -> Principal:
    if not key:
        raise unauthorized()
    info = parse_api_keys_with_roles().get(key)
    if info and check_key_expiry(info):
        return Principal(
            tenant_id=info.tenant_id, api_key=key, user_id=user_id_for_api_key(key), role=info.role
        )
    with ctx.database.transaction(write=True) as session:
        stored = session.scalar(select(ApiKey).where(ApiKey.key_hash == token_digest(key)))
        if stored is None or stored.revoked_at is not None:
            raise unauthorized()
        if stored.expires_at is not None and stored.expires_at <= time.time():
            raise unauthorized()
        stored.last_used_at = time.time()
        return Principal(
            tenant_id=stored.tenant_id,
            user_id=f"api-key:{stored.id}",
            api_key=key,
            role=Role(stored.role),
        )
