"""Unified API response envelope (FR-API-04)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agentic_graphrag.app.contracts import DegradedChannelOut


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class MetaBody(BaseModel):
    total: int | None = None
    page: int | None = None
    limit: int | None = None
    request_id: str | None = None
    # Channels that degraded while answering (O-9). Filled by /v1/query; empty
    # everywhere else. Mirrors data.degraded_channels at envelope level.
    degraded_channels: list[DegradedChannelOut] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class Envelope(BaseModel):
    success: bool
    data: Any | None = None
    error: ErrorBody | None = None
    meta: MetaBody = Field(default_factory=MetaBody)


class TypedEnvelope[T](BaseModel):
    """Published response schema; wire shape stays compatible with Envelope."""

    success: bool
    data: T | None = None
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
) -> dict[str, Any]:
    env = Envelope(
        success=False,
        data=None,
        error=ErrorBody(code=code, message=message, details=details),
        meta=meta or MetaBody(),
    )
    return env.model_dump(mode="json")
