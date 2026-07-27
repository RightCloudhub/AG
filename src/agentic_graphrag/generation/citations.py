"""Citation binding and uncited-assertion intercept (P2-AG-05 / FR-AN-01 / AC-7).

Generation-layer checks: every factual claim must bind to evidence ids that
actually exist in the retrieved candidate set. Failures trigger regenerate
(once) then honest fallback.
"""

from __future__ import annotations

import re

from agentic_graphrag.generation.trace import Claim
from agentic_graphrag.retrieval.contracts import Candidate

# CJK Unified Ideographs, CJK Ext-A, Hangul Syllables, fullwidth forms.
_CJK_RE = re.compile(
    "[\u4e00-\u9fff\u3400-\u4dbf\uac00-\ud7af\u3040-\u309f\u30a0-\u30ff\uff00-\uffef]"
)


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
    """Drop unknown evidence ids from claims (keep claims that still have \u22651)."""
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
    - every claim needs \u22651 evidence_id
    - every claim must reference a retrieved candidate id
    - optional lexical support: claim tokens must overlap cited evidence text
      (not a full NLI check \u2014 still better than ID-only fabrication=0)
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

# Minimum non-stopword, length-≥4 tokens required for Latin claims.
_MIN_LATIN_OVERLAP = 2
# Minimum fraction of claim's significant tokens that must appear in evidence.
_MIN_LATIN_RATIO = 0.30


def _significant_tokens(text: str) -> set[str]:
    """Significant Latin tokens: non-stopword, length ≥ 4."""
    toks: set[str] = set()
    for raw in text.lower().replace("-", " ").split():
        t = _alphanumeric(raw)
        if len(t) >= 4 and t not in _STOPWORDS:
            toks.add(t)
    return toks


def _claim_supported_char(
    claim: Claim,
    by_id: dict[str, Candidate],
    *,
    min_overlap: int = 1,
) -> bool:
    """Char-level (CJK / fallback) support: claim tokens overlap evidence tokens."""
    claim_toks = _content_tokens(claim.text)
    if not claim_toks:
        return True  # nothing to check
    for eid in claim.evidence_ids:
        cand = by_id.get(eid)
        if cand is None:
            continue
        ev_toks = _content_tokens(cand.content)
        if len(claim_toks & ev_toks) >= min_overlap:
            return True
    return False


def _claim_supported_latin(
    claim: Claim,
    by_id: dict[str, Candidate],
) -> bool:
    """Latin support: significant (≥4 char, non-stopword) token overlap."""
    claim_sig = _significant_tokens(claim.text)
    if not claim_sig:
        return True  # nothing significant — skip
    sig_threshold = max(_MIN_LATIN_OVERLAP, int(len(claim_sig) * _MIN_LATIN_RATIO) + 1)
    for eid in claim.evidence_ids:
        cand = by_id.get(eid)
        if cand is None:
            continue
        ev_sig = _significant_tokens(cand.content)
        if len(claim_sig & ev_sig) >= sig_threshold:
            return True
    return False


def claims_lexically_supported(
    claims: list[Claim],
    evidence: list[Candidate],
    *,
    min_overlap: int = 1,
) -> bool:
    """True if every claim shares meaningful content tokens with cited evidence.

    Fixes BL-13: a single shared word (e.g. 'Apex') no longer suffices for
    Latin text.  CJK claims continue to use single-char token matching (BL-02
    behaviour).  For Latin, requires at least ``_MIN_LATIN_OVERLAP`` significant
    tokens (length ≥ 4, non-stopword) with a minimum overlap ratio.
    """
    by_id = {c.id: c for c in evidence}
    for claim in claims:
        if any(_CJK_RE.match(ch) for ch in claim.text):
            if not _claim_supported_char(claim, by_id, min_overlap=min_overlap):
                return False
            continue
        if not _claim_supported_latin(claim, by_id):
            return False
    return True


def _content_tokens(text: str) -> set[str]:
    """Content tokens \u2014 CJK chars as single-char tokens, Latin as >=2 words.

    Fixes BL-02: pure-CJK claims like '\u82f9\u679c\u63a7\u80a1\u7684\u9996\u5e2d\u6267\u884c\u5b98'
    previously collapsed to a single whitespace-delimited 'blob' that never matched
    evidence tokens, causing the lexical-support gate to fail deterministically.
    """
    if not text:
        return set()
    cjk_toks = {ch for ch in text if _CJK_RE.match(ch)}
    if cjk_toks:
        return cjk_toks
    toks: set[str] = set()
    for raw in text.lower().replace("-", " ").split():
        t = _alphanumeric(raw)
        if len(t) >= 2 and t not in _STOPWORDS:
            toks.add(t)
    return toks


def _alphanumeric(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())
