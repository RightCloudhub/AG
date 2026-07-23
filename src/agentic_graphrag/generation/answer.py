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


def _format_evidence(evidence: list[Candidate]) -> str:
    lines = []
    for c in evidence:
        lines.append(f"[{c.id}] ({c.type}, score={c.score:.3f}) {c.content[:500]}")
    return "\n".join(lines) if lines else "(no evidence)"


def _split(text: str) -> tuple[str, str]:
    if "# System" in text and "# User" in text:
        parts = text.split("# User", 1)
        return parts[0].replace("# System", "", 1).strip(), parts[1].strip()
    return "You generate grounded answers.", text


def _apply_payload(
    chain: ReasoningChain,
    payload: AnswerPayload,
    evidence: list[Candidate],
) -> ReasoningChain | None:
    """Apply a validated payload; return None if citation gate fails."""
    if payload.status == QueryStatus.NO_ANSWER:
        chain.honest_fallback(payload.answer or "model reported no answer")
        return chain

    # Clean unknown ids, then validate
    claims = filter_claim_evidence_ids(payload.claims, evidence)
    require = payload.status in (QueryStatus.ANSWERED, QueryStatus.PARTIAL)
    reason = validate_answered_claims(claims, evidence, require_claims=require)
    if reason:
        return None

    chain.answer = payload.answer
    chain.status = payload.status
    chain.claims = claims
    chain.missing_info = payload.missing_info
    return chain


def _llm_answer(
    chain: ReasoningChain,
    evidence: list[Candidate],
    llm: LLMProvider,
    *,
    conclusions: str,
    guardrail_status: str,
    regenerate: bool = False,
    tier: Tier = Tier.STRONG,
) -> AnswerPayload:
    prompt = load_prompt("answer")
    extra = ""
    if regenerate:
        extra = (
            "\n\nIMPORTANT: Your previous answer had uncited claims. "
            "Every claim MUST include evidence_ids that appear in the evidence list above."
        )
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
    applied = _apply_payload(chain, payload, evidence)
    if applied is not None:
        return applied

    # One regenerate attempt (P2-AG-05)
    chain.metadata["citation_intercept"] = True
    chain.metadata["citation_intercept_reason"] = (
        validate_answered_claims(
            filter_claim_evidence_ids(payload.claims, evidence),
            evidence,
            require_claims=True,
        )
        or "citation gate failed"
    )
    retry = _llm_answer(
        chain,
        evidence,
        llm,
        conclusions=conclusions,
        guardrail_status=guardrail_status,
        regenerate=True,
        tier=tier,
    )
    applied = _apply_payload(chain, retry, evidence)
    if applied is not None:
        chain.metadata["citation_regenerated"] = True
        return applied

    chain.honest_fallback("answer claims lacked evidence citations after regenerate")
    chain.metadata["citation_fallback"] = True
    return chain
