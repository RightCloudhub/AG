"""Multi-level budget control: tenant / user / query (FR-OP-02 / P3-OP-02)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from agentic_graphrag.llm.budget import BudgetExceeded, BudgetTracker


@dataclass
class BudgetLimits:
    max_llm_calls: int = 20
    max_tokens: int = 50_000
    max_cost_units: float = 1.0  # abstract cost units per window


@dataclass
class WindowUsage:
    llm_calls: int = 0
    tokens: int = 0
    cost_units: float = 0.0
    window_start: float = field(default_factory=time.time)

    def reset_if_needed(self, window_seconds: float) -> None:
        if time.time() - self.window_start >= window_seconds:
            self.llm_calls = 0
            self.tokens = 0
            self.cost_units = 0.0
            self.window_start = time.time()


class MultiLevelBudget:
    """Hard caps at tenant → user → single-query levels."""

    def __init__(
        self,
        *,
        tenant_limits: BudgetLimits | None = None,
        user_limits: BudgetLimits | None = None,
        query_limits: BudgetLimits | None = None,
        window_seconds: float = 86400.0,
    ) -> None:
        self.tenant_limits = tenant_limits or BudgetLimits(
            max_llm_calls=10_000, max_tokens=5_000_000, max_cost_units=1000.0
        )
        self.user_limits = user_limits or BudgetLimits(
            max_llm_calls=500, max_tokens=200_000, max_cost_units=50.0
        )
        self.query_limits = query_limits or BudgetLimits()
        self.window_seconds = window_seconds
        self._tenant: dict[str, WindowUsage] = {}
        self._user: dict[str, WindowUsage] = {}
        self._lock = threading.Lock()
        self.trips: list[dict[str, Any]] = []

    def query_tracker(self) -> BudgetTracker:
        return BudgetTracker(
            max_llm_calls=self.query_limits.max_llm_calls,
            max_tokens=self.query_limits.max_tokens,
        )

    def check_and_reserve(
        self,
        *,
        tenant_id: str = "default",
        user_id: str = "anonymous",
        estimated_calls: int = 1,
        estimated_tokens: int = 0,
        estimated_cost: float = 0.01,
    ) -> None:
        """Raise BudgetExceeded if any level would be breached."""
        with self._lock:
            t_usage = self._tenant.setdefault(tenant_id, WindowUsage())
            u_usage = self._user.setdefault(f"{tenant_id}:{user_id}", WindowUsage())
            t_usage.reset_if_needed(self.window_seconds)
            u_usage.reset_if_needed(self.window_seconds)

            self._assert_level(
                "tenant",
                tenant_id,
                t_usage,
                self.tenant_limits,
                estimated_calls,
                estimated_tokens,
                estimated_cost,
            )
            self._assert_level(
                "user",
                user_id,
                u_usage,
                self.user_limits,
                estimated_calls,
                estimated_tokens,
                estimated_cost,
            )

    def commit(
        self,
        *,
        tenant_id: str = "default",
        user_id: str = "anonymous",
        llm_calls: int = 0,
        tokens: int = 0,
        cost_units: float = 0.0,
    ) -> None:
        with self._lock:
            t_usage = self._tenant.setdefault(tenant_id, WindowUsage())
            u_usage = self._user.setdefault(f"{tenant_id}:{user_id}", WindowUsage())
            t_usage.reset_if_needed(self.window_seconds)
            u_usage.reset_if_needed(self.window_seconds)
            t_usage.llm_calls += llm_calls
            t_usage.tokens += tokens
            t_usage.cost_units += cost_units
            u_usage.llm_calls += llm_calls
            u_usage.tokens += tokens
            u_usage.cost_units += cost_units

    def _assert_level(
        self,
        level: str,
        key: str,
        usage: WindowUsage,
        limits: BudgetLimits,
        calls: int,
        tokens: int,
        cost: float,
    ) -> None:
        if usage.llm_calls + calls > limits.max_llm_calls:
            self._trip(level, key, "max_llm_calls")
            raise BudgetExceeded(
                f"{level} budget exceeded: max_llm_calls ({key})",
                BudgetTracker(max_llm_calls=limits.max_llm_calls, max_tokens=limits.max_tokens),
            )
        if usage.tokens + tokens > limits.max_tokens:
            self._trip(level, key, "max_tokens")
            raise BudgetExceeded(
                f"{level} budget exceeded: max_tokens ({key})",
                BudgetTracker(max_llm_calls=limits.max_llm_calls, max_tokens=limits.max_tokens),
            )
        if usage.cost_units + cost > limits.max_cost_units:
            self._trip(level, key, "max_cost_units")
            raise BudgetExceeded(
                f"{level} budget exceeded: max_cost_units ({key})",
                BudgetTracker(max_llm_calls=limits.max_llm_calls, max_tokens=limits.max_tokens),
            )

    def _trip(self, level: str, key: str, reason: str) -> None:
        self.trips.append(
            {"level": level, "key": key, "reason": reason, "ts": time.time()}
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "tenants": {k: vars(v) for k, v in self._tenant.items()},
                "users": {k: vars(v) for k, v in self._user.items()},
                "trips": list(self.trips[-50:]),
            }
