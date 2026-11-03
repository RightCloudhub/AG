"""Disjoint branch allowances and usage receipts; no shared counter mutation."""

from agentic_graphrag.agent.blackboard import BudgetEntry, Role, usage_totals
from agentic_graphrag.agent.research_state import ResearchTask
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.llm.roles import model_name, usage_cost


def remaining_budget(task: ResearchTask, receipts: list[BudgetEntry]) -> BudgetTracker:
    totals = usage_totals(receipts)
    return BudgetTracker(
        max_llm_calls=max(0, task.call_limit - totals["llm_calls"]),
        max_tokens=max(0, task.token_limit - totals["total_tokens"]),
    )


def receipt(
    task: ResearchTask,
    budget: BudgetTracker,
    *,
    suffix: str,
    role: Role = "researcher",
    llm: LLMClient | None = None,
    tool_calls: int = 0,
) -> BudgetEntry:
    return BudgetEntry(
        entry_id=f"{task.branch_id}:{suffix}",
        branch_id=task.branch_id,
        tenant_id=task.tenant_id,
        run_id=task.run_id,
        req_id=task.requirement.req_id,
        role=role,
        model=model_name(llm) if llm else "",
        calls=budget.llm_calls,
        prompt_tokens=budget.prompt_tokens,
        completion_tokens=budget.completion_tokens,
        tool_calls=tool_calls,
        cost_usd=_cost(budget, llm),
    )


def _cost(budget: BudgetTracker, llm: LLMClient | None) -> float | None:
    if not budget.total_tokens:
        return 0.0
    # Embedding prices may differ from chat prices. Never label their cost as chat.
    if llm is None or budget.embed_calls:
        return None
    return usage_cost(llm, budget.prompt_tokens, budget.completion_tokens)


def allocation(task: ResearchTask) -> BudgetEntry:
    return BudgetEntry(
        entry_id=f"{task.branch_id}:allocation",
        branch_id=task.branch_id,
        tenant_id=task.tenant_id,
        run_id=task.run_id,
        req_id=task.requirement.req_id,
        role="researcher",
        kind="allocation",
        calls=task.call_limit,
        token_limit=task.token_limit,
        tool_calls=task.tool_limit,
    )
