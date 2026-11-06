"""Shadow recommendations and calibration derived from persisted human decisions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.app.model_metadata import role_revision
from agentic_graphrag.app.review_app import ReviewApp
from agentic_graphrag.app.review_scope import review_service
from agentic_graphrag.config import runtime_dir
from agentic_graphrag.core.models import ModelRole
from agentic_graphrag.core.review_advice import ReviewAdvice, ReviewCalibration, ReviewPair
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.knowledge.review.advisor import ReviewAdvisor, review_fingerprint
from agentic_graphrag.knowledge.review.calibration import apply_if_allowed, calibrate
from agentic_graphrag.knowledge.review.queue import ReviewItem
from agentic_graphrag.llm.roles import for_role
from agentic_graphrag.stores.review_advice import SQLiteReviewAdviceStore

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


@contextmanager
def advice_store(svc: QueryService) -> Iterator[SQLiteReviewAdviceStore]:
    store = SQLiteReviewAdviceStore(runtime_dir(svc.cfg) / "review_advice.sqlite3")
    try:
        yield store
    finally:
        store.close()


def review_item(svc: QueryService, review_id: str) -> ReviewItem:
    item = svc.review_queue.get(review_id) if svc.review_queue else None
    if item is None:
        raise ApiError("INVALID_INPUT", "Unknown review item", status_code=404)
    return item


def advise(svc: QueryService, review_id: str) -> tuple[ReviewAdvice, str]:
    item = review_item(svc, review_id)
    selected = review_service(svc, item)
    kb = selected.knowledge_base
    assert kb is not None
    if not selected.allow_llm:
        raise ApiError(
            "CAPABILITY_UNAVAILABLE", "Review advice requires enabled models", status_code=503
        )
    if item.status != "pending":
        raise ValueError("Generate shadow advice before the human decision")
    budget = BudgetTracker(
        max_llm_calls=selected.cfg.guardrails.max_llm_calls,
        max_tokens=selected.cfg.guardrails.max_tokens_per_query,
    )
    llm = selected._build_llm(budget)

    def approve(row: ReviewItem, advice: ReviewAdvice) -> None:
        ReviewApp(svc).decide(
            row.id,
            "approve",
            tenant_id=kb.tenant_id,
            reviewer="agent:calibrated",
            note=advice.reason,
        )

    try:
        with advice_store(svc) as store:
            advisor = ReviewAdvisor(
                kb,
                for_role(llm, ModelRole.EXTRACTOR),
                for_role(llm, ModelRole.VERIFIER),
                role_revision(selected, ModelRole.EXTRACTOR),
                role_revision(selected, ModelRole.VERIFIER),
                store,
            )
            recommendation = advisor.advise(item, selected.bundle.docs)
            result = apply_if_allowed(
                item,
                recommendation,
                knowledge_base=kb,
                calibration=store.get_calibration(kb.scope),
                policy=selected.cfg.knowledge.agent_review,
                approve=approve,
            )
            return recommendation, result
    finally:
        if (close := getattr(llm, "close", None)) is not None:
            close()


def save_calibration(
    svc: QueryService, baseline: ReviewCalibration, *, review_ids: list[str]
) -> ReviewCalibration:
    kb = svc.knowledge_base
    assert kb is not None
    with advice_store(svc) as store:
        pairs = [_pair(svc, store, review_id=key, baseline=baseline) for key in review_ids]
        report = calibrate(pairs, baseline)
        store.save_calibration(report)
        return report


def _pair(
    svc: QueryService,
    store: SQLiteReviewAdviceStore,
    *,
    review_id: str,
    baseline: ReviewCalibration,
) -> ReviewPair:
    item = review_item(svc, review_id)
    advice = store.get(baseline.scope, review_id)
    if (
        item.status not in {"approved", "rejected"}
        or not item.reviewer
        or item.reviewer.startswith("agent:")
    ):
        raise ValueError("Calibration requires completed human reviews")
    if advice is None or advice.input_fingerprint != review_fingerprint(item):
        raise ValueError("Calibration needs advice for the same reviewed input")
    if (advice.proposer_revision, advice.verifier_revision) != (
        baseline.proposer_revision,
        baseline.verifier_revision,
    ):
        raise ValueError("Calibration cannot mix model revisions")
    return ReviewPair(
        review_id=review_id,
        agent_decision=advice.decision,
        human_decision="approve" if item.status == "approved" else "reject",
    )
