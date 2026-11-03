"""Final cited answer; unresolved requirements always remain visible."""

from dataclasses import replace

from agentic_graphrag.agent.blackboard import usage_totals
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.register_answer import annotate_register, answer_from_register
from agentic_graphrag.agent.requirements import Requirement
from agentic_graphrag.agent.research_budget import receipt
from agentic_graphrag.agent.research_state import ResearchTask
from agentic_graphrag.agent.state import AgentState
from agentic_graphrag.config_agent import AgentConfig
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.llm.scoped import scoped_client


def write_answer(
    state: AgentState,
    llm: LLMClient,
    *,
    config: AgentConfig,
    answer_policy: str,
    context_limit: int,
    guards: GuardrailConfig,
) -> AgentState:
    state = {
        **state,
        "register_runtime": replace(state["register_runtime"], answer_policy=answer_policy),
    }
    task = _writer_task(state, config, guards)
    budget = BudgetTracker(max_llm_calls=task.call_limit, max_tokens=task.token_limit)
    client = scoped_client(llm, budget, max_output_tokens=config.max_output_tokens)
    chain = answer_from_register(
        state,
        client,
        allow_llm=bool(task.call_limit and task.token_limit),
        context_limit=context_limit,
        offline_fallback=False,
    )
    if chain.metadata.get("llm_degraded"):
        chain.honest_fallback("writer unavailable")
        chain = annotate_register(chain, state)
    entry = receipt(task, budget, suffix="usage", role="writer")
    ledger = [*state["budget_ledger"], entry]
    chain.metadata.update(
        agent_mode="multi",
        budget_tier=state.get("budget_tier", "standard"),
        blackboard_events=state["events"],
        budget_ledger=ledger,
        conflicts=state.get("conflicts", {}),
        max_parallel=config.max_parallel,
    )
    totals = usage_totals(ledger)
    chain.cost.llm_calls = totals["llm_calls"]
    chain.cost.tokens = totals["total_tokens"]
    chain.cost.prompt_tokens = totals["prompt_tokens"]
    chain.cost.completion_tokens = totals["completion_tokens"]
    return {"chain": chain, "budget_ledger": [entry], "done": True}


def _writer_task(state: AgentState, config: AgentConfig, guards: GuardrailConfig) -> ResearchTask:
    totals = usage_totals(state["budget_ledger"])
    return ResearchTask(
        branch_id=f"{state['run_id']}:writer",
        run_id=state["run_id"],
        tenant_id=state["tenant_id"],
        question=state["question"],
        requirement=Requirement(req_id="writer", text=state["question"]),
        round=state["hop"],
        token_limit=max(
            0, min(config.writer_reserve_tokens, guards.max_tokens - totals["total_tokens"])
        ),
        call_limit=max(
            0, min(config.writer_reserve_calls, guards.max_llm_calls - totals["llm_calls"])
        ),
        tool_limit=0,
    )
