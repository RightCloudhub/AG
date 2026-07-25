"""Protocol abstractions for scheduling, budget, and audit sinks (ENT-05c)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LimiterStore(Protocol):
    """Rate-limiter protocol: acquire a slot or return an error string."""

    def acquire(self, tenant_id: str) -> str | None: ...

    def release(self, tenant_id: str) -> None: ...


@runtime_checkable
class BudgetStore(Protocol):
    """Multi-level budget protocol: reserve → commit/release."""

    def check_and_reserve(
        self,
        *,
        tenant_id: str,
        user_id: str,
        estimated_calls: int,
        estimated_tokens: int,
        estimated_cost: float,
    ) -> None: ...

    def commit(
        self,
        *,
        tenant_id: str,
        user_id: str,
        llm_calls: int,
        tokens: int,
        cost_units: float,
        reserved_calls: int,
        reserved_tokens: int,
        reserved_cost: float,
    ) -> None: ...

    def release(
        self,
        *,
        tenant_id: str,
        user_id: str,
        reserved_calls: int,
        reserved_tokens: int,
        reserved_cost: float,
    ) -> None: ...

    def snapshot(self) -> dict[str, Any]: ...


@runtime_checkable
class AuditSink(Protocol):
    """Audit event sink protocol."""

    def record(self, event: dict[str, Any]) -> None: ...

    def list_events(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...
