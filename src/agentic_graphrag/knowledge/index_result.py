"""Index results and task completion rules."""

from dataclasses import dataclass, field
from typing import Any

from agentic_graphrag.core.enums import IngestStatus

INDEXED_MESSAGE = "Indexed successfully"
REVIEW_MESSAGE_PREFIX = "Indexed; "


@dataclass
class IngestResult:
    doc_id: str
    chunks_created: int = 0
    chunks_indexed: int = 0
    error: str | None = None
    graph: dict[str, Any] | None = None
    counts: dict[str, int] = field(default_factory=dict)
    fatal: bool = False
    # The document cannot fit the token budget, so it never had chunks or vectors.
    unchunkable: bool = False

    @property
    def review_items(self) -> int:
        return int((self.graph or {}).get("conflicts_review", 0))


def task_outcome(
    results: list[IngestResult], *, pending_reviews: int | None = None
) -> tuple[IngestStatus, str]:
    failed = [r for r in results if r.fatal]
    if failed:
        return IngestStatus.FAILED, failed[0].error or "index_failed"
    return _nonfatal_outcome(results, pending_reviews=pending_reviews)


def _nonfatal_outcome(
    results: list[IngestResult], *, pending_reviews: int | None
) -> tuple[IngestStatus, str]:
    incomplete = sum(r.counts.get("incomplete", 0) for r in results)
    if incomplete or any(r.error for r in results):
        reasons = "; ".join(sorted(filter(None, (r.error for r in results))))
        return IngestStatus.PARTIAL, f"{incomplete} incomplete chunk(s); {reasons}"
    pending = sum(r.review_items for r in results) if pending_reviews is None else pending_reviews
    if pending:
        return IngestStatus.REVIEW, f"{REVIEW_MESSAGE_PREFIX}{pending} conflict(s) await review"
    return IngestStatus.DONE, _done_message(results)


def _done_message(results: list[IngestResult]) -> str:
    facts = sum(int((r.graph or {}).get("accepted", 0)) for r in results)
    suffix = f"; {facts} graph fact(s) written" if any(r.graph is not None for r in results) else ""
    return INDEXED_MESSAGE + suffix


def total_counts(results: list[IngestResult]) -> dict[str, int]:
    keys = {key for result in results for key in result.counts}
    return {key: sum(r.counts.get(key, 0) for r in results) for key in keys}
