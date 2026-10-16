"""LLM entailment judge for answer claims (docs/design/BUSINESS_LOGIC.md BL-13).

The rule gates (citation binding, lexical support, object anchoring, relation
consistency) are cheap proxies. On the live path this module adds the real
check: one LIGHT-tier call judges every claim against the evidence it cites
and labels it ``entailed`` / ``contradicted`` / ``not_enough_info``. Any label
other than ``entailed`` rejects the payload with a reason the regenerate pass
can act on.

Cost: one LIGHT call per answered payload (claims are batched). Disable with
``llm.entailment_check: false``. A judge that errors or returns unparsable
output *abstains* (the payload is kept) and the abstention is reported in the
chain metadata — the judge must never turn an outage into "cannot answer".
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import Claim

logger = logging.getLogger(__name__)

NOT_ENTAILED = "claim not entailed by cited evidence"
JUDGE_ABSTAINED = "entailment_judge_abstained"
_EVIDENCE_CHARS = 400

REPAIR_HINTS: dict[str, str] = {
    NOT_ENTAILED: (
        "A fact-checker found that one or more claims are not entailed by the evidence "
        "they cite (the evidence is about something else, or says the opposite). Only "
        "state what the cited evidence literally says; otherwise drop the claim and "
        "lower status to partial or no_answer."
    ),
}


def _system_prompt() -> str:
    """Fact-checker system prompt from the prompt registry (configs/prompts/entailment.md)."""
    from agentic_graphrag.config import load_prompt

    return load_prompt("entailment").replace("# System", "", 1).strip()


class Verdict(BaseModel):
    index: int
    label: Literal["entailed", "contradicted", "not_enough_info"]


class VerdictPayload(BaseModel):
    verdicts: list[Verdict] = Field(default_factory=list)


def entailment_enabled(llm: Any) -> bool:
    if llm is None or not getattr(llm, "supports_entailment", True):
        return False
    from agentic_graphrag.config import get_config

    return bool(getattr(get_config().llm, "entailment_check", True))


def judge_claims(claims: list[Claim], evidence: list[Candidate], llm: Any) -> tuple[str, dict]:
    """Return ``(failure_reason, report)``; reason is ``""`` when all claims pass."""
    if not claims or not entailment_enabled(llm):
        return "", {}
    try:
        payload = _ask(claims, evidence, llm)
    except Exception as exc:  # noqa: BLE001 — abstain, never fail the answer on judge error
        from agentic_graphrag.governance.budget import BudgetExceeded

        if isinstance(exc, BudgetExceeded):
            raise
        logger.warning("Entailment judge abstained: %s", exc)
        return "", {JUDGE_ABSTAINED: type(exc).__name__}
    failed = [v.index for v in payload.verdicts if v.label != "entailed"]
    report = {"entailment": {str(v.index): v.label for v in payload.verdicts}}
    return (NOT_ENTAILED if failed else ""), report


def _ask(claims: list[Claim], evidence: list[Candidate], llm: Any) -> VerdictPayload:
    from agentic_graphrag.llm.provider import Message, Tier
    from agentic_graphrag.llm.structured import complete_structured

    by_id = {c.id: c for c in evidence}
    blocks = [_claim_block(i, claim, by_id) for i, claim in enumerate(claims)]
    messages = [
        Message(role="system", content=_system_prompt()),
        Message(role="user", content="\n\n".join(blocks)),
    ]
    return complete_structured(llm, messages, VerdictPayload, tier=Tier.LIGHT, max_retries=1)


def _claim_block(index: int, claim: Claim, by_id: dict[str, Candidate]) -> str:
    cited = [by_id[e] for e in claim.evidence_ids if e in by_id]
    lines = [f"Claim {index}: {claim.text}", "Cited evidence:"]
    lines += [f"- [{c.id}] {(c.content or '')[:_EVIDENCE_CHARS]}" for c in cited]
    return "\n".join(lines)
