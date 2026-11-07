"""Validate a human's merge target before making the review decision terminal."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic_graphrag.core.enums import ReviewDecision, ReviewType

if TYPE_CHECKING:
    from agentic_graphrag.knowledge.review.queue import ReviewItem


class InvalidReviewSelection(ValueError):
    """The selected target is missing or outside the staged candidates."""


def merge_selection(
    item: ReviewItem, decision: ReviewDecision, *, keep_id: str | None = None
) -> str | None:
    applicable = (
        item.type == ReviewType.RESOLUTION
        and item.payload.get("open_resolution")
        and decision == ReviewDecision.APPROVE
    )
    if not applicable:
        if keep_id is not None:
            raise InvalidReviewSelection(
                "A candidate can only be selected for an open merge approval"
            )
        return None
    selected = keep_id if keep_id is not None else item.payload.get("keep_id")
    if (
        not isinstance(selected, str)
        or not selected
        or selected == item.payload.get("drop_id")
        or selected not in item.payload.get("candidate_ids", [])
    ):
        raise InvalidReviewSelection("Select a valid candidate keep_id before approving this merge")
    return selected
