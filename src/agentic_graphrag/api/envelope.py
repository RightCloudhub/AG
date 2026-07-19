"""Unified API response envelope (FR-API-04)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class MetaBody(BaseModel):
    total: int | None = None
    page: int | None = None
    limit: int | None = None
    request_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Envelope(BaseModel):
    success: bool
    data: Any | None = None
    error: ErrorBody | None = None
    meta: MetaBody = Field(default_factory=MetaBody)


def ok(data: Any, *, meta: MetaBody | None = None) -> dict[str, Any]:
    env = Envelope(success=True, data=data, error=None, meta=meta or MetaBody())
    return env.model_dump(mode="json")


def fail(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    meta: MetaBody | None = None,
    status_hint: int | None = None,
) -> dict[str, Any]:
    del status_hint  # reserved for route layer mapping
    env = Envelope(
        success=False,
        data=None,
        error=ErrorBody(code=code, message=message, details=details),
        meta=meta or MetaBody(),
    )
    return env.model_dump(mode="json")
