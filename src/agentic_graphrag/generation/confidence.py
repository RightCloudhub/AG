"""Answer confidence grading (FR-AN-05 / P5-CAP-03)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from agentic_graphrag.generation.trace import QueryStatus, ReasoningChain
from agentic_graphrag.retrieval.contracts import Candidate


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


def grade_confidence(
    chain: ReasoningChain,
    evidence: list[Candidate] | None = None,
) -> dict[str, Any]:
    """Heuristic confidence grade from evidence density, claims, and status."""
    evidence = evidence or []
    if chain.status == QueryStatus.NO_ANSWER:
        return {
            "level": ConfidenceLevel.NONE.value,
            "score": 0.0,
            "reasons": ["no_answer"],
        }

    score = 0.0
    reasons: list[str] = []

    graph_n = sum(1 for c in evidence if c.is_graph())
    if graph_n >= 2:
        score += 0.35
        reasons.append("multi_graph_evidence")
    elif graph_n == 1:
        score += 0.2
        reasons.append("single_graph_evidence")

    if chain.claims:
        cited = sum(1 for c in chain.claims if c.evidence_ids)
        ratio = cited / max(len(chain.claims), 1)
        score += 0.3 * ratio
        reasons.append(f"claim_citation_ratio={ratio:.2f}")

    if chain.status == QueryStatus.ANSWERED:
        score += 0.2
        reasons.append("status_answered")
    elif chain.status == QueryStatus.PARTIAL:
        score += 0.05
        reasons.append("status_partial")

    hops = len(chain.steps)
    if hops >= 2:
        score += 0.1
        reasons.append("multi_hop")

    score = max(0.0, min(1.0, score))
    if score >= 0.75:
        level = ConfidenceLevel.HIGH
    elif score >= 0.45:
        level = ConfidenceLevel.MEDIUM
    elif score > 0:
        level = ConfidenceLevel.LOW
    else:
        level = ConfidenceLevel.NONE

    return {"level": level.value, "score": round(score, 3), "reasons": reasons}
