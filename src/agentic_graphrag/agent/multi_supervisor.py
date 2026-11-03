"""Code-driven scheduling, budget allocation and explicit terminal reasons."""

import time
from dataclasses import replace

from agentic_graphrag.agent.blackboard import usage_totals
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.multi_conflicts import blocked_requirements
from agentic_graphrag.agent.register_helpers import stopped_state
from agentic_graphrag.agent.research_budget import allocation
from agentic_graphrag.agent.research_state import ResearchTask
from agentic_graphrag.agent.state import AgentState, active_register
from agentic_graphrag.config_agent import AgentConfig
from agentic_graphrag.core.enums import RequirementStatus


def stop_reason(state: AgentState, *, guards: GuardrailConfig) -> str:
    register = active_register(state)
    if register.is_clear():
        return _clear_reason(state)
    if state.get("deadline", 0) and time.time() >= state["deadline"]:
        return "timeout"
    if state["hop"] >= min(guards.max_hops, guards.requirement_register.max_rounds):
        return "round_budget"
    totals = usage_totals(state.get("budget_ledger", []))
    if totals["total_tokens"] >= guards.max_tokens:
        return "token_budget"
    if totals["llm_calls"] >= guards.max_llm_calls:
        return "call_budget"
    blocked = blocked_requirements(register, state.get("conflicts", {}))
    if not any(item.req_id not in blocked for item in register.ready_items()):
        return "dependency_blocked"
    return ""


def _clear_reason(state: AgentState) -> str:
    rejected = state["register_runtime"].rejections
    capped = any(
        r.req_id in rejected and r.status == RequirementStatus.RETIRED
        for r in active_register(state).items.values()
    )
    return "verifier_rejection_cap" if capped else "clear"


def supervise(state: AgentState, *, config: AgentConfig, guards: GuardrailConfig) -> AgentState:
    reason = stop_reason(state, guards=guards)
    if reason:
        return {**stopped_state(state, reason), "tasks": []}
    register = active_register(state)
    blocked = blocked_requirements(register, state.get("conflicts", {}))
    attempts = dict(state["register_runtime"].attempts)
    ready = sorted(
        (r for r in register.ready_items() if r.req_id not in blocked),
        key=lambda r: (attempts.get(r.req_id, 0), r.req_id),
    )
    totals = usage_totals(state.get("budget_ledger", []))
    calls = guards.max_llm_calls - totals["llm_calls"] - config.writer_reserve_calls - 1
    tokens = guards.max_tokens - totals["total_tokens"] - config.writer_reserve_tokens
    tokens -= config.verifier_reserve_tokens
    if calls <= 0 or tokens <= config.max_output_tokens:
        return {
            **stopped_state(state, "call_budget" if calls <= 0 else "token_budget"),
            "tasks": [],
        }
    ready = ready[:calls]
    tasks = [
        ResearchTask(
            branch_id=f"{state['run_id']}:{state['hop'] + 1}:{r.req_id}",
            run_id=state["run_id"],
            tenant_id=state["tenant_id"],
            question=state["question"],
            requirement=register.search_target(r).model_copy(deep=True),
            round=state["hop"] + 1,
            branch_count=len(ready),
            known_evidence_ids=sorted(state["evidence"]),
            token_limit=min(config.branch_tokens, tokens // len(ready)),
            call_limit=min(config.max_research_calls, calls // len(ready)),
            tool_limit=config.max_tool_calls,
        )
        for r in ready
    ]
    for task in tasks:
        rid = task.requirement.req_id
        attempts[rid] = attempts.get(rid, 0) + 1
    return {
        "hop": state["hop"] + 1,
        "tasks": tasks,
        "verifications": [],
        "budget_ledger": [allocation(task) for task in tasks],
        "register_runtime": replace(state["register_runtime"], attempts=attempts),
    }
