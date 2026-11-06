"""Settle issued calls even when their result arrives after timeout or disconnect."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic_graphrag.app.wiring import cost_units_for_chain
from agentic_graphrag.governance.execution import ExecutionControl
from agentic_graphrag.observability.degradation import record_degradation

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def close_client(control: ExecutionControl) -> None:
    close = getattr(control.llm, "close", None)
    if close is not None:
        try:
            close()
        except Exception as exc:
            record_degradation("llm", "client_close_failed", exc)


def settle_aborted(
    svc: QueryService, control: ExecutionControl, *, tenant_id: str, user_id: str
) -> None:
    control.cancelled.set()

    def finish(_future: object = None) -> None:
        budget = control.budget
        try:
            if svc.multi_budget is not None and budget is not None and not control.settled:
                svc.multi_budget.commit(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    llm_calls=budget.llm_calls,
                    tokens=budget.total_tokens,
                    cost_units=cost_units_for_chain(budget.llm_calls + budget.embed_calls),
                )
            control.settled = True
        finally:
            close_client(control)

    if control.pending is not None and not control.pending.done():
        control.pending.add_done_callback(finish)
    else:
        finish()
