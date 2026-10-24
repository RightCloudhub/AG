"""Pure filtering helpers for ``ReviewQueue`` (kept apart for the size budget)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentic_graphrag.knowledge.review.queue import ReviewItem


def tenant_allows(item: ReviewItem, tenant_id: str | None) -> bool:
    """True when ``tenant_id`` may act on ``item`` (empty item tenant = legacy row)."""
    if tenant_id is None or not item.tenant_id:
        return True
    return item.tenant_id == tenant_id


def filter_items(
    items: list[ReviewItem],
    *,
    status: str | None,
    type: str | None,
    min_confidence: float | None,
    max_confidence: float | None,
    tenant_id: str | None,
    batch_id: str | None = None,
) -> list[ReviewItem]:
    def matches(item: ReviewItem) -> bool:
        return all(
            (
                batch_id is None or item.batch_id == batch_id,
                not status or item.status == status,
                not type or item.type == type,
                min_confidence is None or item.confidence >= min_confidence,
                max_confidence is None or item.confidence <= max_confidence,
                tenant_id is None or item.tenant_id == tenant_id,
            )
        )

    return [item for item in items if matches(item)]
