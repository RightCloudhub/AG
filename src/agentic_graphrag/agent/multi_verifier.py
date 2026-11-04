"""One isolated batched judge call; verdicts are proposals for the merge node."""

from agentic_graphrag.agent.blackboard import BranchEvent, BudgetEntry, Proposal
from agentic_graphrag.agent.register_llm import evidence_input, json_text
from agentic_graphrag.agent.research_budget import receipt
from agentic_graphrag.agent.research_state import (
    ResearchTask,
    VerificationBatch,
    VerificationResult,
)
from agentic_graphrag.agent.state import AgentState
from agentic_graphrag.config import load_prompt
from agentic_graphrag.config_agent import AgentConfig
from agentic_graphrag.core.enums import LLMRole, MessageRole, PromptName, RequirementStatus
from agentic_graphrag.core.ports.llm import LLMClient, Message
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.llm.roles import model_name
from agentic_graphrag.llm.scoped import scoped_client
from agentic_graphrag.llm.structured import complete_structured
from agentic_graphrag.observability.degradation import (
    REASON_VERIFIER_UNAVAILABLE,
    record_degradation,
)


def verify_batch(
    state: AgentState,
    llm: LLMClient,
    *,
    config: AgentConfig,
    root_budget: BudgetTracker | None = None,
) -> AgentState:
    proposals = [
        p
        for p in state["proposals"]
        if p.round == state["hop"] and p.requirement.status == RequirementStatus.RESOLVED
    ]
    if not proposals:
        return {"verifications": []}
    evidence = state["evidence"]
    budget = BudgetTracker(
        max_llm_calls=1, max_tokens=config.verifier_reserve_tokens, parent=root_budget
    )
    task = ResearchTask(
        branch_id=f"{state['run_id']}:verify:{state['hop']}",
        run_id=state["run_id"],
        tenant_id=state["tenant_id"],
        question="",
        requirement=proposals[0].requirement,
        round=state["hop"],
        token_limit=budget.max_tokens,
        call_limit=1,
        tool_limit=0,
    )
    verdicts = _judge(proposals, state, llm=llm, config=config, budget=budget)
    invalid = {
        p.entry_id
        for p in proposals
        if not p.requirement.passages or p.requirement.passages - evidence.keys()
    }
    verdicts = [
        v.model_copy(update={"entailed": False, "reason": "missing supporting evidence"})
        if v.proposal_id in invalid
        else v
        for v in verdicts
    ]
    entries = _receipts(proposals, receipt(task, budget, suffix="usage", role="verifier", llm=llm))
    return {
        "verifications": verdicts,
        "budget_ledger": entries,
        "events": _events(task, proposals, verdicts, model=model_name(llm), entries=entries),
    }


def _receipts(proposals: list[Proposal], entry: BudgetEntry) -> list[BudgetEntry]:
    """Split batch tokens equally (remainder first); count its single call once."""
    count = len(proposals)
    return [
        entry.model_copy(
            update={
                "entry_id": f"{entry.entry_id}:{p.requirement.req_id}",
                "req_id": p.requirement.req_id,
                "calls": entry.calls if i == 0 else 0,
                "prompt_tokens": entry.prompt_tokens // count + (i < entry.prompt_tokens % count),
                "completion_tokens": entry.completion_tokens // count
                + (i < entry.completion_tokens % count),
                "cost_usd": entry.cost_usd / count if entry.cost_usd is not None else None,
            }
        )
        for i, p in enumerate(proposals)
    ]


def _inputs(proposals: list[Proposal], state: AgentState) -> list[dict[str, object]]:
    evidence = state["evidence"]
    return [
        {
            "proposal_id": p.entry_id,
            "requirement": p.requirement.text,
            "passages": evidence_input(
                [evidence[i] for i in sorted(p.requirement.passages) if i in evidence]
            ),
        }
        for p in proposals
    ]


def _judge(
    proposals: list[Proposal],
    state: AgentState,
    *,
    llm: LLMClient,
    config: AgentConfig,
    budget: BudgetTracker,
) -> list[VerificationResult]:
    try:
        user = load_prompt(PromptName.VERIFY_BATCH).format(
            input_json=json_text(_inputs(proposals, state))
        )
        if len(user.encode("utf-8")) + config.max_output_tokens > budget.max_tokens:
            raise ValueError("verifier context exceeds reserved token budget")
        client = scoped_client(llm, budget, max_output_tokens=config.max_output_tokens)
        batch = complete_structured(
            client,
            [Message(role=MessageRole.USER, content=user)],
            VerificationBatch,
            max_retries=0,
            temperature=0,
            role=LLMRole.REGISTER_VERIFY,
        )
        if sorted(v.proposal_id for v in batch.verdicts) != sorted(p.entry_id for p in proposals):
            raise ValueError("verifier omitted or duplicated a proposal")
        verdicts = batch.verdicts
    except Exception as exc:
        record_degradation("multi_verifier", REASON_VERIFIER_UNAVAILABLE, exc)
        verdicts = [
            VerificationResult(proposal_id=p.entry_id, entailed=False, reason=type(exc).__name__)
            for p in proposals
        ]
    return verdicts


def _events(
    task: ResearchTask,
    proposals: list[Proposal],
    verdicts: list[VerificationResult],
    *,
    model: str,
    entries: list[BudgetEntry],
) -> list[BranchEvent]:
    by_id = {v.proposal_id: v for v in verdicts}
    return [
        BranchEvent(
            entry_id=f"{task.branch_id}:{p.requirement.req_id}",
            branch_id=p.branch_id,
            tenant_id=task.tenant_id,
            run_id=task.run_id,
            req_id=p.requirement.req_id,
            role="verifier",
            model=model,
            tokens=entries[i].tokens,
            round=task.round,
            outcome="accepted" if by_id[p.entry_id].entailed else "rejected",
            detail=by_id[p.entry_id].reason,
        )
        for i, p in enumerate(proposals)
    ]
