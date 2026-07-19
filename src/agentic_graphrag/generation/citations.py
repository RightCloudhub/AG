"""Citation binding and uncited-assertion intercept (P2-AG-05 / FR-AN-01 / AC-7).

Generation-layer checks: every factual claim must bind to evidence ids that
actually exist in the retrieved candidate set. Failures trigger regenerate
(once) then honest fallback.
"""

from __future__ import annotations

from agentic_graphrag.generation.trace import Claim
from agentic_graphrag.retrieval.contracts import Candidate


def evidence_id_set(evidence: list[Candidate]) -> set[str]:
    return {c.id for c in evidence}


def claims_have_citations(claims: list[Claim]) -> bool:
    """True iff every claim has at least one non-empty evidence id."""
    if not claims:
        return False
    return all(bool(c.evidence_ids) for c in claims)


def claims_bind_to_evidence(
    claims: list[Claim],
    evidence: list[Candidate] | set[str],
) -> bool:
    """True iff every claim cites at least one id present in ``evidence``."""
    if not claims:
        return False
    known = evidence if isinstance(evidence, set) else evidence_id_set(evidence)
    if not known:
        return False
    for claim in claims:
        if not claim.evidence_ids:
            return False
        if not any(eid in known for eid in claim.evidence_ids):
            return False
    return True


def filter_claim_evidence_ids(
    claims: list[Claim],
    evidence: list[Candidate] | set[str],
) -> list[Claim]:
    """Drop unknown evidence ids from claims (keep claims that still have ≥1)."""
    known = evidence if isinstance(evidence, set) else evidence_id_set(evidence)
    cleaned: list[Claim] = []
    for claim in claims:
        ids = [eid for eid in claim.evidence_ids if eid in known]
        if ids:
            cleaned.append(Claim(text=claim.text, evidence_ids=ids))
    return cleaned


def bind_claims_to_evidence(
    claims: list[Claim],
    evidence: list[Candidate],
    *,
    fallback_text: str | None = None,
) -> list[Claim]:
    """Ensure claims are citation-bound; synthesize one claim if empty but evidence exists.

    Used by offline answerers that produce text without structured claims.
    """
    cleaned = filter_claim_evidence_ids(claims, evidence)
    if cleaned:
        return cleaned
    if not evidence:
        return []
    text = (fallback_text or evidence[0].content)[:500]
    return [Claim(text=text, evidence_ids=[c.id for c in evidence[:5]])]


class CitationInterceptError(ValueError):
    """Raised when an answered payload has uncited or unbound claims."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def validate_answered_claims(
    claims: list[Claim],
    evidence: list[Candidate],
    *,
    require_claims: bool = True,
) -> str | None:
    """Return a failure reason, or None if claims pass the generation gate.

    Rules (AC-7 baseline):
    - ANSWERED/PARTIAL paths that assert facts must carry claims
    - every claim needs ≥1 evidence_id
    - every claim must reference a retrieved candidate id
    """
    if not claims:
        return "no claims" if require_claims else None
    if not claims_have_citations(claims):
        return "claim missing evidence_ids"
    if not claims_bind_to_evidence(claims, evidence):
        return "claim evidence_ids not in retrieved set"
    return None
