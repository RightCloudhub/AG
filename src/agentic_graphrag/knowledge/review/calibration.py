"""Held-out paired decisions gate automation; confidence is never an approval label."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable

from agentic_graphrag.config_review import ReviewAutomationConfig
from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.core.review_advice import ReviewAdvice, ReviewCalibration, ReviewPair
from agentic_graphrag.knowledge.review.advisor import review_fingerprint
from agentic_graphrag.knowledge.review.queue import ReviewItem

WILSON_Z_95 = 1.959963984540054
HASH_MAX = float(2**256)


def calibrate(pairs: list[ReviewPair], baseline: ReviewCalibration) -> ReviewCalibration:
    if len({pair.review_id for pair in pairs}) != len(pairs):
        raise ValueError("Repeated review items cannot inflate calibration support")
    approved = sum(pair.agent_decision == "approve" for pair in pairs)
    false = sum(
        pair.agent_decision == "approve" and pair.human_decision == "reject" for pair in pairs
    )
    return baseline.model_copy(
        update={
            "reviewed": len(pairs),
            "approved": approved,
            "false_approvals": false,
            "false_approval_upper": _wilson_upper(false, approved),
        }
    )


def _wilson_upper(errors: int, total: int) -> float:
    if total == 0:
        return 1.0
    probability, z2 = errors / total, WILSON_Z_95**2
    center = probability + z2 / (2 * total)
    spread = WILSON_Z_95 * math.sqrt(probability * (1 - probability) / total + z2 / (4 * total**2))
    return min(1.0, (center + spread) / (1 + z2 / total))


def automation_blocker(
    item: ReviewItem,
    advice: ReviewAdvice,
    *,
    knowledge_base: KnowledgeBase,
    calibration: ReviewCalibration | None,
    policy: ReviewAutomationConfig,
) -> str | None:
    if not policy.enabled:
        return "automation_disabled"
    if item.status != "pending" or item.type != "extraction" or not item.payload.get("staged"):
        return "review_type_not_allowed"
    relation = (item.payload.get("triple") or {}).get("relation")
    if relation not in policy.relation_allowlist:
        return "relation_not_allowlisted"
    if not _advice_matches(item, advice, knowledge_base=knowledge_base):
        return "advice_not_verified_for_current_input"
    if reason := _calibration_blocker(advice, calibration, policy=policy):
        return reason
    fraction = (
        int(hashlib.sha256(f"{item.id}:{advice.proposer_revision}".encode()).hexdigest(), 16)
        / HASH_MAX
    )
    return "selected_for_human_sample" if fraction < policy.sample_rate else None


def _advice_matches(
    item: ReviewItem, advice: ReviewAdvice, *, knowledge_base: KnowledgeBase
) -> bool:
    return all(
        (
            advice.review_id == item.id,
            advice.scope == knowledge_base.scope,
            advice.generation == knowledge_base.active_generation,
            advice.verified,
            advice.decision == "approve",
            advice.input_fingerprint == review_fingerprint(item),
            advice.proposer_revision != advice.verifier_revision,
        )
    )


def _calibration_blocker(
    advice: ReviewAdvice, calibration: ReviewCalibration | None, *, policy: ReviewAutomationConfig
) -> str | None:
    if calibration is None or not calibration.real_corpus or not calibration.held_out:
        return "real_held_out_calibration_required"
    if (
        calibration.scope != advice.scope
        or calibration.proposer_revision != advice.proposer_revision
        or calibration.verifier_revision != advice.verifier_revision
    ):
        return "calibration_model_or_scope_mismatch"
    if calibration.reviewed < policy.minimum_reviewed:
        return "insufficient_paired_reviews"
    if calibration.false_approval_upper > policy.max_false_approval_upper:
        return "false_approval_bound_exceeds_policy"
    return None


def apply_if_allowed(
    item: ReviewItem,
    advice: ReviewAdvice,
    *,
    knowledge_base: KnowledgeBase,
    calibration: ReviewCalibration | None,
    policy: ReviewAutomationConfig,
    approve: Callable[[ReviewItem, ReviewAdvice], None],
) -> str:
    blocker = automation_blocker(
        item, advice, knowledge_base=knowledge_base, calibration=calibration, policy=policy
    )
    if blocker:
        return blocker
    approve(item, advice)
    return "approved_with_audit"
