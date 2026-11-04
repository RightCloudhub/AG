"""Settle ingestion only after all current-generation review candidates are counted."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic_graphrag.knowledge.index_result import IngestResult, task_outcome, total_counts
from agentic_graphrag.knowledge.ingest_tasks import IngestStatus, IngestTask
from agentic_graphrag.knowledge.review.queue import ReviewType

if TYPE_CHECKING:
    from agentic_graphrag.knowledge.ingest_worker import IngestWorker


def finalize_task(worker: IngestWorker, task: IngestTask, results: list[IngestResult]) -> None:
    # Decisions use the same lock so they cannot race this transition.
    with worker.doc_store.lock():
        queue = worker.graph_ingestor.review_queue if worker.graph_ingestor else None
        pending = (
            queue.count(status="pending", tenant_id=task.tenant_id, batch_id=task.id)
            - queue.count(
                status="pending",
                type=ReviewType.SPOTCHECK,
                tenant_id=task.tenant_id,
                batch_id=task.id,
            )
            if queue is not None
            else None
        )
        status, message = task_outcome(results, pending_reviews=pending)
        if status == IngestStatus.DONE and worker.before_done is not None:
            worker.before_done(task)
        worker._finalize(task.id, status, message, counts=total_counts(results))
