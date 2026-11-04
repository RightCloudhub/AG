"""A recorded decision must take effect before its batch can be published."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic_graphrag.knowledge.review.queue import ReviewItem, ReviewQueue, ReviewType

if TYPE_CHECKING:
    from agentic_graphrag.knowledge.review.executor import ReviewExecutor


def ready_review_task(executor: ReviewExecutor, item: ReviewItem) -> bool:
    tasks, queue = executor.task_store, executor.review_queue
    if tasks is None or queue is None or not item.batch_id:
        return False
    task = tasks.get(item.batch_id, tenant_id=item.tenant_id or "default")
    if task is None or task.status != "review":
        return False
    pending = queue.count(status="pending", batch_id=item.batch_id, tenant_id=item.tenant_id)
    pending -= queue.count(
        status="pending",
        type=ReviewType.SPOTCHECK.value,
        batch_id=item.batch_id,
        tenant_id=item.tenant_id,
    )
    return not pending and not has_unexecuted_reviews(queue, item)


def has_unexecuted_reviews(queue: ReviewQueue, item: ReviewItem) -> bool:
    _, total = queue.list_page(status=None, tenant_id=item.tenant_id, limit=1)
    rows = queue.list(status=None, tenant_id=item.tenant_id, limit=max(1, total))
    return any(
        row.batch_id == item.batch_id
        and row.type != ReviewType.SPOTCHECK
        and not (row.execution.get("applied") or row.execution.get("completed"))
        for row in rows
    )
