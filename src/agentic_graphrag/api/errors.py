"""API error codes and exception types."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """Raised inside handlers; converted to envelope by exception handlers."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


# Machine-readable codes (api-and-ui.md)
INVALID_INPUT = "INVALID_INPUT"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
TIMEOUT_PARTIAL = "TIMEOUT_PARTIAL"
RATE_LIMITED = "RATE_LIMITED"
INTERNAL_ERROR = "INTERNAL_ERROR"
SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
