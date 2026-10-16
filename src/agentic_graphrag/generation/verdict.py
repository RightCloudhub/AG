"""Verification verdicts: phrasing problems vs. missing evidence (refactor §6.1/§6.2).

A rejected answer is either a *phrasing* problem the composer can repair in
place (no claims, bad/missing citation ids) or an *evidence* problem no amount
of rewording fixes (claim not supported / not entailed by what was retrieved).
Evidence problems are reported as a structured :class:`EvidenceGap` so the
agent can go back to retrieval instead of regenerating.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from agentic_graphrag.core.reasoning import QueryStatus, ReasoningChain
from agentic_graphrag.generation.entailment import NOT_ENTAILED

EVIDENCE_GAP_KEY = "evidence_gap"

PHRASING_REASONS = frozenset(
    {
        "no claims",
        "claim missing evidence_ids",
        "claim evidence_ids not in retrieved set",
    }
)
EVIDENCE_REASONS = frozenset(
    {
        "claim text not supported by cited evidence content",
        NOT_ENTAILED,
    }
)


class VerdictKind(StrEnum):
    OK = "ok"
    PHRASING = "phrasing"
    EVIDENCE = "evidence"


def classify_rejection(reason: str) -> VerdictKind:
    if not reason:
        return VerdictKind.OK
    if reason in EVIDENCE_REASONS:
        return VerdictKind.EVIDENCE
    return VerdictKind.PHRASING


@dataclass
class EvidenceGap:
    reason: str
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def record_gap(chain: ReasoningChain, gap: EvidenceGap) -> None:
    chain.metadata = {**(chain.metadata or {}), EVIDENCE_GAP_KEY: gap.to_dict()}


def evidence_gap(chain: ReasoningChain) -> EvidenceGap | None:
    """The gap the composer reported, or one implied by a partial answer."""
    raw = (chain.metadata or {}).get(EVIDENCE_GAP_KEY)
    if isinstance(raw, dict) and raw.get("missing"):
        return EvidenceGap(reason=str(raw.get("reason") or ""), missing=list(raw["missing"]))
    if chain.status in (QueryStatus.PARTIAL, QueryStatus.NO_ANSWER) and chain.missing_info:
        return EvidenceGap(reason="partial_answer", missing=list(chain.missing_info))
    return None


def report_gap(chain: ReasoningChain, reason: str, missing: list[str]) -> bool:
    """Record an evidence gap on ``chain`` when ``reason`` is an evidence problem
    and the model named something new to look for. Returns whether it did."""
    if classify_rejection(reason) is not VerdictKind.EVIDENCE:
        return False
    q = chain.question.strip().lower()
    novel = [m for m in missing if m.strip() and m.strip().lower() != q]
    if not novel:
        return False
    chain.status = QueryStatus.PARTIAL
    chain.missing_info = list(missing)
    record_gap(chain, EvidenceGap(reason=reason, missing=novel))
    return True
