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
    require_lexical_support: bool = True,
) -> str | None:
    """Return a failure reason, or None if claims pass the generation gate.

    Rules (AC-7 baseline):
    - ANSWERED/PARTIAL paths that assert facts must carry claims
    - every claim needs ≥1 evidence_id
    - every claim must reference a retrieved candidate id
    - optional lexical support: claim tokens must overlap cited evidence text
      (not a full NLI check — still better than ID-only fabrication=0)
    """
    if not claims:
        return "no claims" if require_claims else None
    if not claims_have_citations(claims):
        return "claim missing evidence_ids"
    if not claims_bind_to_evidence(claims, evidence):
        return "claim evidence_ids not in retrieved set"
    if require_lexical_support and not claims_lexically_supported(claims, evidence):
        return "claim text not supported by cited evidence content"
    return None


_STOPWORDS = frozenset(
    "a an the of to in on for and or is are was were be by with from as at".split()
)


def _has_cjk(text: str) -> bool:
    """True if text contains any CJK character (used for cross-language bridging)."""
    return any(_is_cjk(ch) for ch in text)


def claims_lexically_supported(
    claims: list[Claim],
    evidence: list[Candidate],
    *,
    min_overlap: int = 2,
) -> bool:
    """True if every claim shares ≥2 content tokens with at least one cited candidate.

    ⚠ This is a lexical overlap check, NOT a full NLI / relationship-verification
    gate. A claim that correctly names the subject and object entities but asserts
    the wrong relationship type can still pass (e.g., evidence says "FOUNDED_BY"
    but claim says "CEO_OF" — as long as entity names overlap). See BL-13.

    The CJK-unigram expansion (see ``_content_tokens``) means single Chinese
    characters count as separate tokens, so ``min_overlap=2`` still gives a
    weak check for East Asian languages.

    **Cross-language bridging (BL-02 fix):** When a claim and its cited evidence
    are in different language families (e.g., Chinese-only claim vs English-only
    evidence, or vice versa), lexical overlap is fundamentally impossible. In
    that case the check is skipped — the evidence-binding check in
    ``validate_answered_claims`` already ensures the claim references real
    retrieved evidence, which provides a baseline level of verification.
    """
    by_id = {c.id: c for c in evidence}
    for claim in claims:
        claim_toks = _content_tokens(claim.text)
        if not claim_toks:
            continue
        claim_has_cjk = _has_cjk(claim.text)
        supported = False
        for eid in claim.evidence_ids:
            cand = by_id.get(eid)
            if cand is None:
                continue
            ev_toks = _content_tokens(cand.content)
            if len(claim_toks & ev_toks) >= min_overlap:
                supported = True
                break
            # Cross-language bridging: if claim has CJK but evidence doesn't
            # (or vice versa), lexical overlap is impossible — skip the check.
            ev_has_cjk = _has_cjk(cand.content)
            if claim_has_cjk != ev_has_cjk:
                supported = True
                break
        if not supported:
            return False
    return True


def _content_tokens(text: str) -> set[str]:
    toks = set()
    for raw in (text or "").lower().replace("-", " ").split():
        t = "".join(ch for ch in raw if ch.isalnum())
        if len(t) >= 2 and t not in _STOPWORDS:
            toks.add(t)
    # CJK-aware: individual CJK characters are meaningful tokens (unigrams).
    # Whitespace-based splitting collapses CJK sentences into single tokens;
    # this ensures Chinese claims can find lexical overlap with evidence.
    for ch in (text or ""):
        if _is_cjk(ch):
            toks.add(ch)
    return toks


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    # CJK Unified Ideographs, Extension A, Compatibility Ideographs, Radicals
    return (
        (0x4E00 <= cp <= 0x9FFF)
        or (0x3400 <= cp <= 0x4DBF)
        or (0xF900 <= cp <= 0xFAFF)
        or (0x2E80 <= cp <= 0x2EFF)
    )
