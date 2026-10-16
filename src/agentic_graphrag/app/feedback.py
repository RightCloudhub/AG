"""Answer feedback use case (FR-OP-03): link feedback to a chain, enqueue bad answers."""

from __future__ import annotations

from typing import Any

from agentic_graphrag.knowledge.review.queue import ReviewType


def submit_feedback(
    svc: Any,
    query_id: str,
    *,
    accurate: bool,
    reason: str = "",
    user_id: str = "",
    tenant_id: str = "default",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query_id": query_id,
        "accurate": accurate,
        "reason": reason,
        "user_id": user_id,
    }
    if svc.review_queue is not None and not accurate:
        item = svc.review_queue.enqueue(
            ReviewType.FEEDBACK,
            payload,
            confidence=0.0,
            tenant_id=tenant_id,
        )
        payload["review_id"] = item.id
    _attach_feedback_to_audit(svc, query_id, payload)
    return payload


def _attach_feedback_to_audit(svc: Any, query_id: str, payload: dict[str, Any]) -> None:
    if svc.audit_store is None:
        return
    chain = svc.audit_store.get(query_id)
    if chain is None:
        return
    meta = dict(chain.get("metadata") or {})
    feedbacks = list(meta.get("feedback") or [])
    feedbacks.append(payload)
    meta["feedback"] = feedbacks
    chain["metadata"] = meta
    svc.audit_store.save(chain)
