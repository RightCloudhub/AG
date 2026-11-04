"""Apply request overrides and strategy budgets consistently to REST and SSE."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.app.contracts import QueryRequest
from agentic_graphrag.app.global_query import configure_global_budget
from agentic_graphrag.core.constants import MILLISECONDS_PER_SECOND
from agentic_graphrag.governance.budget import BudgetTracker

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def guard_and_budget(svc: QueryService, req: QueryRequest) -> tuple[GuardrailConfig, BudgetTracker]:
    timeout = (
        max(1, math.ceil(req.timeout_ms / MILLISECONDS_PER_SECOND))
        if req.timeout_ms is not None
        else None
    )
    guard = GuardrailConfig.from_app_config(
        svc.cfg, max_hops=req.max_hops, query_timeout_seconds=timeout
    )
    budget = svc.multi_budget.query_tracker() if svc.multi_budget else guard.budget_tracker()
    configure_global_budget(svc, req, budget)
    return guard, budget
