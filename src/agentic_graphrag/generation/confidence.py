"""Answer confidence grading (FR-AN-05 / P5-CAP-03)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from agentic_graphrag.generation.trace import QueryStatus, ReasoningChain
from agentic_graphrag.retrieval.contracts import Candidate

_HIGH = 0.75
_MEDIUM = 0.45
_MULTI_GRAPH = 0.35
_SINGLE_GRAPH = 0.2
_CLAIM_WEIGHT = 0.3
_ANSWERED = 0.2
_PARTIAL = 0.05
_MULTI_HOP = 0.1
_GRAPH_MULTI_MIN = 2
_HOP_MULTI_MIN = 2


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
        return {"level": ConfidenceLevel.NONE.value, "score": 0.0, "reasons": ["no_answer"]}

    score = 0.0
    reasons: list[str] = []
    score += _graph_score(evidence, reasons)
    score += _claim_score(chain, reasons)
    score += _status_score(chain, reasons)
    if len(chain.steps) >= _HOP_MULTI_MIN:
        score += _MULTI_HOP
        reasons.append("multi_hop")
    score = max(0.0, min(1.0, score))
    return {"level": _level_for(score).value, "score": round(score, 3), "reasons": reasons}


def _graph_score(evidence: list[Candidate], reasons: list[str]) -> float:
    graph_n = sum(1 for c in evidence if c.is_graph())
    if graph_n >= _GRAPH_MULTI_MIN:
        reasons.append("multi_graph_evidence")
        return _MULTI_GRAPH
    if graph_n == 1:
        reasons.append("single_graph_evidence")
        return _SINGLE_GRAPH
    return 0.0


def _claim_score(chain: ReasoningChain, reasons: list[str]) -> float:
    if not chain.claims:
        return 0.0
    cited = sum(1 for c in chain.claims if c.evidence_ids)
    ratio = cited / max(len(chain.claims), 1)
    reasons.append(f"claim_citation_ratio={ratio:.2f}")
    return _CLAIM_WEIGHT * ratio


def _status_score(chain: ReasoningChain, reasons: list[str]) -> float:
    if chain.status == QueryStatus.ANSWERED:
        reasons.append("status_answered")
        return _ANSWERED
    if chain.status == QueryStatus.PARTIAL:
        reasons.append("status_partial")
        return _PARTIAL
    return 0.0


def _level_for(score: float) -> ConfidenceLevel:
    if score >= _HIGH:
        return ConfidenceLevel.HIGH
    if score >= _MEDIUM:
        return ConfidenceLevel.MEDIUM
    if score > 0:
        return ConfidenceLevel.LOW
    return ConfidenceLevel.NONE
