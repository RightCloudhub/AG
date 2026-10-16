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
    preds = _equality_preds({"batch_id": batch_id, "status": status or None, "type": type or None})
    if min_confidence is not None:
        preds.append(lambda i, m=min_confidence: i.confidence >= m)
    if max_confidence is not None:
        preds.append(lambda i, m=max_confidence: i.confidence <= m)
    if tenant_id is not None:
        preds.append(lambda i, t=tenant_id: i.tenant_id == t)
    if not preds:
        return items
    return [i for i in items if all(p(i) for p in preds)]


def _equality_preds(fields: dict[str, str | None]) -> list:
    """``item.<field> == value`` predicates for every field given a value."""
    return [
        (lambda i, f=name, v=value: getattr(i, f) == v)
        for name, value in fields.items()
        if value is not None
    ]
