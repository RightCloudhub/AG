"""Resolve the core API's lightweight tenant context without a Web dependency."""

from fastapi import Request


def request_tenant(request: Request) -> str:
    current = getattr(request.state, "principal", None)
    return str(current.tenant_id) if current is not None else "default"
