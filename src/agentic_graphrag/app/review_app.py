"""ReviewApp: record a human decision and apply it (refactor §0-A / §3.3).

The HTTP route and any CLI/worker share this one use case, so a decision
always lands on the ledger *and* takes effect on the graph, and always emits
``ReviewDecided`` (and ``GraphCommitted`` when it changed facts).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


from agentic_graphrag.app.write_path import review_executor_for
from agentic_graphrag.knowledge.review.queue import ReviewDecision, ReviewItem


class ReviewQueueUnavailable(RuntimeError):
    pass


class ReviewApp:
    def __init__(self, svc: QueryService) -> None:
        self._svc = svc

    def decide(
        self,
        item_id: str,
        decision: ReviewDecision | str,
        *,
        reviewer: str = "",
        note: str = "",
        tenant_id: str | None = None,
    ) -> ReviewItem:
        """Record + execute. Raises ``KeyError`` / ``ReviewAlreadyDecided`` from the queue."""
        queue = self._svc.review_queue
        if queue is None:
            raise ReviewQueueUnavailable("review queue not configured")
        dec = decision if isinstance(decision, ReviewDecision) else ReviewDecision(decision)
        item = queue.decide(item_id, dec, reviewer=reviewer, note=note, tenant_id=tenant_id)
        executor = review_executor_for(self._svc)
        if executor is not None:
            executor.execute(item)
        return item
