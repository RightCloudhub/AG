"""The sole publisher of the multi-agent register, after invariant enforcement."""

from dataclasses import replace

from agentic_graphrag.agent.blackboard import Proposal
from agentic_graphrag.agent.multi_bootstrap import validate_evidence
from agentic_graphrag.agent.multi_conflicts import add_conflicts
from agentic_graphrag.agent.register_helpers import with_register_memory
from agentic_graphrag.agent.register_invariants import apply_invariants
from agentic_graphrag.agent.register_llm import initial_context
from agentic_graphrag.agent.register_progress import progress_state
from agentic_graphrag.agent.requirements import VERIFIER_RETIRE_REASON, Register, Requirement
from agentic_graphrag.agent.research_state import VerificationResult
from agentic_graphrag.agent.state import AgentState
from agentic_graphrag.core.enums import CriticAction, RequirementStatus
from agentic_graphrag.core.reasoning import ReasoningStep


def merge(state: AgentState, *, rejection_cap: int) -> AgentState:
    validate_evidence(state["evidence"], state["tenant_id"])
    old = state.get("register") or Register(question=state["question"])
    proposed = old.model_copy(deep=True)
    proposals = [p for p in state.get("proposals", []) if p.round == state.get("hop", 0)]
    counts = dict(state["register_runtime"].rejections)
    verdicts = {v.proposal_id: v for v in state.get("verifications", [])}
    for proposal in proposals:
        if (proposal.tenant_id, proposal.run_id) != (state["tenant_id"], state["run_id"]):
            raise ValueError("cross-run or cross-tenant proposal rejected")
        item = _verified_item(proposal, verdicts, counts=counts, rejection_cap=rejection_cap)
        proposed.items[item.req_id] = item
    conflicts = add_conflicts(proposed, state.get("proposals", []), evidence=state["evidence"])
    approved = apply_invariants(proposed, old, evidence=state["evidence"])
    runtime = replace(state["register_runtime"], rejections=counts)
    updated: AgentState = {**state, "register_runtime": runtime, "conflicts": conflicts}
    result = progress_state(updated, old, approved, verifier=[], delta_enabled=True)
    if state.get("register") is None:
        result["register_runtime"] = replace(
            runtime,
            context=initial_context(approved),
            events=[
                {
                    "type": "initialization",
                    "round": 0,
                    "error": "",
                    "register": approved,
                    "seed_ids": list(state["evidence"]),
                }
            ],
        )
    chain = state["chain"].model_copy(deep=True)
    chain.steps.extend(_step(p, approved) for p in proposals if p.round)
    return with_register_memory({**result, "chain": chain})


def _verified_item(
    proposal: Proposal,
    verdicts: dict[str, VerificationResult],
    *,
    counts: dict[str, int],
    rejection_cap: int,
) -> Requirement:
    item = proposal.requirement.model_copy(deep=True)
    verdict = verdicts.get(proposal.entry_id)
    if item.status == RequirementStatus.RESOLVED and (verdict is None or not verdict.entailed):
        counts[item.req_id] = counts.get(item.req_id, 0) + 1
        item.status = RequirementStatus.PENDING
        if counts[item.req_id] >= rejection_cap:
            item.status = RequirementStatus.RETIRED
            item.retire_reason = VERIFIER_RETIRE_REASON.format(count=counts[item.req_id])
    return item


def _step(proposal: Proposal, approved: Register) -> ReasoningStep:
    item = approved.items[proposal.requirement.req_id]
    return ReasoningStep(
        hop=proposal.round,
        sub_question=item.text,
        depends_on=item.depends_on,
        tool_calls=proposal.tool_calls,
        evidence_ids=sorted(item.passages),
        conclusion=item.notes,
        critic_action=CriticAction.SUFFICIENT
        if item.status == RequirementStatus.RESOLVED
        else CriticAction.NEXT_HOP,
    )
