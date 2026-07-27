"""Citation binding and uncited-assertion intercept (P2-AG-05 / FR-AN-01 / AC-7).

Generation-layer checks: every factual claim must bind to evidence ids that
actually exist in the retrieved candidate set. Failures trigger regenerate
(once) then honest fallback.
"""

from __future__ import annotations

import re

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

    Caveat (BL-13): lexical support is a proxy, not full NLI. It checks token
    overlap, not whether the relation asserted in the claim matches the
    evidence. A claim citing a real id but misreading its relation can pass.
    True relation-aware checking requires an NLI step (out of scope).
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

# CJK Unified Ideographs + Extension A + Compatibility Ideographs.
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_LATIN_RUN = re.compile(r"[a-z0-9]+")


def claims_lexically_supported(
    claims: list[Claim],
    evidence: list[Candidate],
    *,
    min_overlap: int = 1,
) -> bool:
    """True if every claim shares ≥1 content token with at least one cited candidate.

    Tokenization is CJK-aware (BL-02): CJK runs yield unigrams + bigrams so
    Chinese claims can match Chinese evidence. Latin/digit runs follow the
    whitespace-based split. Note (BL-13): overlap is lexical only; a claim
    that cites a real id but asserts a different relation may still pass.
    """
    by_id = {c.id: c for c in evidence}
    for claim in claims:
        claim_toks = _content_tokens(claim.text)
        if not claim_toks:
            continue
        supported = False
        for eid in claim.evidence_ids:
            cand = by_id.get(eid)
            if cand is None:
                continue
            ev_toks = _content_tokens(cand.content)
            if len(claim_toks & ev_toks) >= min_overlap:
                supported = True
                break
        if not supported:
            return False
    return True


def _content_tokens(text: str) -> set[str]:
    """Token set for lexical support check; CJK-aware (BL-02).

    - Latin/digit runs: lowercase, alnum-filtered, len>=2, non-stopword.
    - CJK runs: unigrams + bigrams of consecutive CJK chars. Whitespace
      splitting alone collapses a whole Chinese sentence into one token,
      making the gate always fail for pure-CJK claims. Bigrams add
      discrimination so the gate is not trivially satisfied (BL-13).
    """
    toks: set[str] = set()
    if not text:
        return toks
    lowered = text.lower().replace("-", " ")
    for raw in lowered.split():
        # Latin/digit sub-tokens (preserves prior behaviour for mixed runs).
        for latin in _LATIN_RUN.findall(raw):
            if len(latin) >= 2 and latin not in _STOPWORDS:
                toks.add(latin)
        # CJK runs: unigrams + bigrams.
        for cjk_run in _CJK_RUN.findall(raw):
            if len(cjk_run) == 1:
                toks.add(cjk_run)
                continue
            for i in range(len(cjk_run)):
                toks.add(cjk_run[i])
                if i + 1 < len(cjk_run):
                    toks.add(cjk_run[i : i + 2])
    return toks
