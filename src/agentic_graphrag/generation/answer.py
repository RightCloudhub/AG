"""Answer generation with citation binding and honest fallback (FR-AN-01 / P2-AG-05).

Key rules:
- every factual claim must bind to retrieved evidence ids
- uncited assertions are intercepted (regenerate once, then honest fallback)
- offline path lives in ``offline_answer.py`` to keep this module under the size budget
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentic_graphrag.config import load_prompt

# Re-export for older imports / tests
from agentic_graphrag.generation.citations import (
    claims_have_citations,  # noqa: F401
    filter_claim_evidence_ids,
    validate_answered_claims,
)
from agentic_graphrag.generation.offline_answer import offline_answer
from agentic_graphrag.generation.trace import Claim, QueryStatus, ReasoningChain
from agentic_graphrag.llm.provider import LLMProvider, Message, Tier
from agentic_graphrag.llm.structured import complete_structured
from agentic_graphrag.retrieval.contracts import Candidate


class AnswerPayload(BaseModel):
    answer: str
    status: QueryStatus
    claims: list[Claim] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)


# Persisted-evidence caps: bound audit-row size without dropping citable rows.
EVIDENCE_CATALOG_LIMIT = 50
EVIDENCE_CONTENT_CHARS = 800


# Keyed by the exact reasons ``validate_answered_claims`` returns. The regenerate
# pass costs a second STRONG call, so it must be told what actually failed: the
# previous generic "add evidence_ids" text could never repair a lexical-support
# failure, which made the retry deterministic waste (docs/BUSINESS_LOGIC.md BL-02).
_REPAIR_HINTS: dict[str, str] = {
    "no claims": (
        "Your previous answer carried no claims at all. Split the answer into short "
        "factual claims and bind each one to the evidence it rests on."
    ),
    "claim missing evidence_ids": (
        "One or more claims had an empty evidence_ids list. Every claim MUST cite at "
        "least one bracketed id from the evidence list above."
    ),
    "claim evidence_ids not in retrieved set": (
        "One or more claims cited an id that does not appear in the evidence list. Use "
        "ONLY the bracketed ids shown above, verbatim; never invent an id."
    ),
    "claim text not supported by cited evidence content": (
        "One or more claims cited evidence whose text does not state what the claim "
        "asserts — the ids were valid but the content did not match. Restate each claim "
        "in the wording of the evidence you cite, naming the exact entity that evidence "
        "points to, and do not lean on an edge that asserts a different relation. If no "
        "evidence states it, drop the claim and lower status to partial or no_answer."
    ),
}
_DEFAULT_REPAIR_HINT = "Re-ground every claim in the evidence above, citing ids exactly as shown."


def _repair_instruction(reason: str) -> str:
    hint = _REPAIR_HINTS.get(reason, _DEFAULT_REPAIR_HINT)
    return f"\n\nIMPORTANT — your previous answer was rejected ({reason}). {hint}"


def _format_evidence(evidence: list[Candidate]) -> str:
    lines = []
    for c in evidence:
        lines.append(f"[{c.id}] ({c.type}, score={c.score:.3f}) {c.content[:500]}")
    return "\n".join(lines) if lines else "(no evidence)"


def _attach_evidence_catalog(chain: ReasoningChain, evidence: list[Candidate]) -> None:
    """Persist evidence id+content so API/UI can resolve citation clicks."""
    catalog = [_catalog_entry(c) for c in evidence[:EVIDENCE_CATALOG_LIMIT]]
    chain.metadata = {**(chain.metadata or {}), "evidence": catalog}


def _catalog_entry(candidate: Candidate) -> dict[str, object]:
    """One catalog row, flagged when its content was cut.

    ``truncated`` is load-bearing downstream: ``eval/metrics_evidence.py``
    re-checks claim↔evidence overlap against *this* persisted text, so a
    supporting token living past the cut would make the eval gate stricter than
    the runtime gate it exists to mirror (BL-13). The flag lets it abstain
    instead of accusing the row of fabricating.

    ``structured`` is persisted for the same reason (BL-13 / D2): the runtime
    object-anchor check reads ``candidate.structured``, so the eval side can
    only reproduce that check when the catalog carries the shape through.
    """
    content = candidate.content or ""
    source = candidate.source
    entry: dict[str, object] = {
        "id": candidate.id,
        "content": content[:EVIDENCE_CONTENT_CHARS],
        "truncated": len(content) > EVIDENCE_CONTENT_CHARS,
        "source": source.value if hasattr(source, "value") else str(source),
        "score": candidate.score,
    }
    structured = getattr(candidate, "structured", None)
    if isinstance(structured, dict) and structured:
        entry["structured"] = structured
    return entry


def _split(text: str) -> tuple[str, str]:
    if "# System" in text and "# User" in text:
        parts = text.split("# User", 1)
        return parts[0].replace("# System", "", 1).strip(), parts[1].strip()
    return "You generate grounded answers.", text


def _apply_payload(
    chain: ReasoningChain,
    payload: AnswerPayload,
    evidence: list[Candidate],
) -> tuple[ReasoningChain | None, str]:
    """Apply a validated payload.

    Returns ``(chain, "")`` on success and ``(None, reason)`` when the citation
    gate rejects it — the caller needs that reason to build a repair hint that
    can actually fix the failure (BL-02), so it is reported, not recomputed.
    """
    if payload.status == QueryStatus.NO_ANSWER:
        chain.honest_fallback(payload.answer or "model reported no answer")
        return chain, ""

    # Clean unknown ids, then validate
    claims = filter_claim_evidence_ids(payload.claims, evidence)
    require = payload.status in (QueryStatus.ANSWERED, QueryStatus.PARTIAL)
    reason = validate_answered_claims(claims, evidence, require_claims=require)
    if reason:
        return None, reason

    chain.answer = payload.answer
    chain.status = payload.status
    chain.claims = claims
    chain.missing_info = payload.missing_info
    return chain, ""


def _llm_answer(
    chain: ReasoningChain,
    evidence: list[Candidate],
    llm: LLMProvider,
    *,
    conclusions: str,
    guardrail_status: str,
    repair_reason: str = "",
    tier: Tier = Tier.STRONG,
) -> AnswerPayload:
    prompt = load_prompt("answer")
    extra = _repair_instruction(repair_reason) if repair_reason else ""
    system, user = _split(
        prompt.format(
            question=chain.question,
            evidence_list=_format_evidence(evidence),
            conclusions=conclusions or "(none)",
            guardrail_status=guardrail_status,
        )
        + extra
    )
    return complete_structured(
        llm,
        [Message(role="system", content=system), Message(role="user", content=user)],
        AnswerPayload,
        tier=tier,
        max_retries=1,  # P95: one repair pass max (was 2 → up to 3 LLM calls)
    )


def generate_answer(
    chain: ReasoningChain,
    evidence: list[Candidate],
    llm: LLMProvider | None,
    *,
    conclusions: str = "",
    guardrail_status: str = "ok",
    allow_llm: bool = True,
    tier: Tier = Tier.STRONG,
) -> ReasoningChain:
    """Generate final answer into the reasoning chain with citation intercept."""
    _attach_evidence_catalog(chain, evidence)
    if not evidence:
        chain.honest_fallback("no evidence retrieved")
        return chain

    if not allow_llm or llm is None:
        return offline_answer(chain, evidence, conclusions)

    # Skip remote call entirely when circuit is already open (P95: avoid 3s connect).
    circuit = getattr(llm, "circuit", None)
    if circuit is not None and not circuit.allow():
        chain.metadata = {
            **(chain.metadata or {}),
            "llm_degraded": True,
            "llm_error": "CircuitOpen",
            "llm_error_msg": "LLM circuit open; offline answer",
        }
        return offline_answer(chain, evidence, conclusions)

    try:
        return _generate_with_llm(
            chain,
            evidence,
            llm,
            conclusions=conclusions,
            guardrail_status=guardrail_status,
            tier=tier,
        )
    except Exception as exc:  # noqa: BLE001 — degrade, do not 500 the API
        # Upstream LLM timeouts / 5xx / circuit open: still answer extractively.
        from agentic_graphrag.llm.budget import BudgetExceeded

        if isinstance(exc, BudgetExceeded):
            raise
        chain.metadata = {
            **(chain.metadata or {}),
            "llm_degraded": True,
            "llm_error": type(exc).__name__,
            "llm_error_msg": str(exc)[:200],
        }
        return offline_answer(chain, evidence, conclusions)


def _generate_with_llm(
    chain: ReasoningChain,
    evidence: list[Candidate],
    llm: LLMProvider,
    *,
    conclusions: str,
    guardrail_status: str,
    tier: Tier,
) -> ReasoningChain:
    payload = _llm_answer(
        chain,
        evidence,
        llm,
        conclusions=conclusions,
        guardrail_status=guardrail_status,
        tier=tier,
    )
    applied, reason = _apply_payload(chain, payload, evidence)
    if applied is not None:
        return applied
    return _regenerate_after_intercept(
        chain,
        evidence,
        llm,
        reason=reason or "citation gate failed",
        conclusions=conclusions,
        guardrail_status=guardrail_status,
        tier=tier,
    )


def _regenerate_after_intercept(
    chain: ReasoningChain,
    evidence: list[Candidate],
    llm: LLMProvider,
    *,
    reason: str,
    conclusions: str,
    guardrail_status: str,
    tier: Tier,
) -> ReasoningChain:
    """One targeted regenerate attempt (P2-AG-05), then honest fallback."""
    chain.metadata["citation_intercept"] = True
    chain.metadata["citation_intercept_reason"] = reason
    retry = _llm_answer(
        chain,
        evidence,
        llm,
        conclusions=conclusions,
        guardrail_status=guardrail_status,
        repair_reason=reason,
        tier=tier,
    )
    applied, retry_reason = _apply_payload(chain, retry, evidence)
    if applied is not None:
        chain.metadata["citation_regenerated"] = True
        return applied

    chain.metadata["citation_retry_reason"] = retry_reason or reason
    chain.honest_fallback("answer claims lacked evidence citations after regenerate")
    chain.metadata["citation_fallback"] = True
    return chain
