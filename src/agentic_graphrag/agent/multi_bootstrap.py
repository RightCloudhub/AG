"""Budgeted seed retrieval and initializer proposals for the single merge writer."""

import time

from agentic_graphrag.agent.blackboard import Proposal, usage_totals
from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.register_llm import initialize
from agentic_graphrag.agent.register_search import seed_search
from agentic_graphrag.agent.requirements import Register, Requirement
from agentic_graphrag.agent.research_budget import receipt
from agentic_graphrag.agent.research_state import ResearchTask
from agentic_graphrag.agent.research_tools import branch_executor
from agentic_graphrag.agent.state import AgentState
from agentic_graphrag.config_agent import AgentConfig
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.governance.budget import BudgetExceeded, BudgetTracker
from agentic_graphrag.llm.scoped import scoped_client


def bootstrap(
    state: AgentState,
    executor: Executor,
    llm: LLMClient,
    *,
    config: AgentConfig,
    guards: GuardrailConfig,
) -> AgentState:
    task = _task(state, config, guards)
    budget = BudgetTracker(max_llm_calls=task.call_limit, max_tokens=task.token_limit)
    local = branch_executor(executor, task, budget)
    hits, _ = (
        seed_search(
            local,
            state["question"],
            top_k=guards.requirement_register.seed_top_k,
            tenant_id=state["tenant_id"],
            cache={},
        )
        if task.token_limit
        else ([], None)
    )
    evidence = {**state["evidence"], **{hit.id: hit for hit in hits}}
    validate_evidence(evidence, state["tenant_id"])
    client = scoped_client(llm, budget, max_output_tokens=config.max_output_tokens)
    try:
        register = initialize(
            state["question"],
            list(evidence.values()),
            client if task.call_limit and task.token_limit else None,
            config=guards.requirement_register,
        )
    except BudgetExceeded:
        register = initialize(state["question"], [], None, config=guards.requirement_register)
    return {
        "evidence": evidence,
        "hop": 0,
        "proposals": _initial_proposals(task, register),
        "budget_ledger": [
            receipt(task, budget, suffix="usage", role="triage", tool_calls=1, llm=llm)
        ],
        "deadline": state.get("deadline", 0)
        or (
            time.time() + guards.query_timeout_seconds if guards.query_timeout_seconds > 0 else 0.0
        ),
    }


def _task(state: AgentState, config: AgentConfig, guards: GuardrailConfig) -> ResearchTask:
    totals = usage_totals(state["budget_ledger"])
    return ResearchTask(
        branch_id=f"{state['run_id']}:init",
        run_id=state["run_id"],
        tenant_id=state["tenant_id"],
        question=state["question"],
        requirement=Requirement(req_id="init", text=state["question"]),
        round=0,
        token_limit=max(
            0,
            min(
                config.branch_tokens,
                guards.max_tokens
                - totals["total_tokens"]
                - config.writer_reserve_tokens
                - config.verifier_reserve_tokens,
            ),
        ),
        call_limit=max(
            0, min(1, guards.max_llm_calls - totals["llm_calls"] - config.writer_reserve_calls - 1)
        ),
        tool_limit=1,
    )


def validate_evidence(evidence: dict[str, Candidate], tenant_id: str) -> None:
    for key, hit in evidence.items():
        if key != hit.id or str(hit.metadata.get("tenant_id", tenant_id)) != tenant_id:
            raise ValueError("evidence identity does not match the requested tenant or chunk")


def _initial_proposals(task: ResearchTask, register: Register) -> list[Proposal]:
    return [
        Proposal(
            entry_id=f"{task.branch_id}:{rid}",
            branch_id=task.branch_id,
            tenant_id=task.tenant_id,
            run_id=task.run_id,
            requirement=item,
        )
        for rid, item in register.items.items()
    ]
