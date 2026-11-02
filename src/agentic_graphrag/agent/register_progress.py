"""Approved progress messages, verification events and context accounting."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.register_invariants import schedule
from agentic_graphrag.agent.register_llm import initial_context, json_text, verify
from agentic_graphrag.agent.requirements import VERIFIER_RETIRE_REASON, Register, Requirement, diff
from agentic_graphrag.agent.state import AgentState
from agentic_graphrag.config import load_prompt
from agentic_graphrag.config_register import RegisterVerifierConfig
from agentic_graphrag.core.enums import CriticAction, MessageRole, PromptName, RequirementStatus
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.budget import BudgetExceeded, BudgetTracker

MAX_VERIFIER_WORKERS = 8


def context_size(context: list[dict[str, str]], extra: str = "") -> int:
    """Conservative UTF-8 byte bound, rather than an optimistic chars/4 estimate."""
    messages = list(context)
    if extra:
        messages.append({"role": MessageRole.USER, "content": extra})
    return len(json_text(messages).encode("utf-8"))


def verify_resolutions(
    new: Register,
    old: Register,
    llm: LLMClient,
    *,
    evidence: dict[str, Candidate],
    context_limit: int,
    config: RegisterVerifierConfig | None = None,
    rejections: dict[str, int] | None = None,
    budget: BudgetTracker | None = None,
) -> list[JsonObject]:
    config = config or RegisterVerifierConfig()
    jobs = [(rid, item) for rid, item in new.items.items() if _needs_verification(item, old, rid)]
    if not jobs:
        return []
    _check_batch_budget(budget, len(jobs))
    with ThreadPoolExecutor(
        max_workers=MAX_VERIFIER_WORKERS, thread_name_prefix="agr-verify"
    ) as pool:
        futures = [
            pool.submit(
                copy_context().run,
                _verify_one,
                item,
                evidence,
                llm,
                context_limit=context_limit,
                temperature=config.temperature,
            )
            for _, item in jobs
        ]
        # Collect before mutating: a budget error keeps the previous approved state.
        reports = [future.result() for future in futures]
    counts = rejections if rejections is not None else {}
    events = [
        _apply_verdict(rid, item, report, counts=counts, limit=config.max_rejections)
        for (rid, item), report in zip(jobs, reports, strict=True)
    ]
    schedule(new)
    return events


def _needs_verification(item: Requirement, old: Register, rid: str) -> bool:
    previous = old.items.get(rid)
    return item.status == RequirementStatus.RESOLVED and (
        previous is None or previous.snapshot() != item.snapshot()
    )


def _check_batch_budget(budget: BudgetTracker | None, calls: int) -> None:
    if budget is None:
        return
    budget.check()
    if budget.llm_calls + calls > budget.max_llm_calls:
        raise BudgetExceeded("insufficient calls for register verifier batch", budget)


def _apply_verdict(
    rid: str, item: Requirement, report: JsonObject, *, counts: dict[str, int], limit: int
) -> JsonObject:
    if not report["entailed"]:
        counts[rid] = counts.get(rid, 0) + 1
        item.status = RequirementStatus.PENDING
        if counts[rid] >= limit:
            item.status = RequirementStatus.RETIRED
            item.retire_reason = VERIFIER_RETIRE_REASON.format(count=counts[rid])
    return {
        "type": "verification",
        "req_id": rid,
        "passages": sorted(item.passages),
        **report,
        "accepted": report["entailed"],
        "rejections": counts.get(rid, 0),
        "retire_reason": item.retire_reason,
    }


def _verify_one(
    item: Requirement,
    evidence: dict[str, Candidate],
    llm: LLMClient,
    *,
    context_limit: int,
    temperature: float,
) -> JsonObject:
    if item.passages - evidence.keys():
        return {
            "entailed": False,
            "reason": "supporting evidence unavailable",
            "error": "missing_evidence",
        }
    supporting = [evidence[i].model_dump(mode="json") for i in sorted(item.passages)]
    if (
        context_size(
            [], json_text(supporting) + item.text + load_prompt(PromptName.REGISTER_VERIFY)
        )
        > context_limit
    ):
        return {"entailed": False, "reason": "verifier context limit", "error": "context_limit"}
    try:
        return verify(item, evidence, llm, temperature=temperature).model_dump()
    except BudgetExceeded:
        raise
    except Exception as exc:
        return {
            "entailed": False,
            "reason": "verifier could not establish sufficiency",
            "error": type(exc).__name__,
        }


def nudge_budget(state: AgentState, *, ratio: float, limit: int) -> AgentState:
    if state.get("register_budget_nudged") or state.get("hop", 0) < math.ceil(ratio * limit):
        return state
    context = list(state.get("register_context") or [])
    context.append({"role": MessageRole.USER, "content": load_prompt(PromptName.REGISTER_BUDGET)})
    return {**state, "register_context": context, "register_budget_nudged": True}


def progress_state(
    state: AgentState,
    old: Register,
    new: Register,
    *,
    verifier: list[JsonObject],
    delta_enabled: bool,
) -> AgentState:
    changes = diff(old, new)
    context = list(state.get("register_context") or [])
    text = format_progress(changes, new) if changes else ""
    if text and delta_enabled:
        context.append({"role": MessageRole.USER, "content": text})
    elif not delta_enabled:
        context = initial_context(new)
        if state.get("register_budget_nudged"):
            context.append(
                {"role": MessageRole.USER, "content": load_prompt(PromptName.REGISTER_BUDGET)}
            )
    event = {
        "type": "progress",
        "round": state.get("hop", 0),
        "changes": changes,
        "verifier": verifier,
        "new_passages": state.get("register_fresh", []),
    }
    chain = ReasoningChain.model_validate(state["chain"])
    chain.steps[-1].conclusion = text or "No requirement was closed."
    chain.steps[-1].critic_action = (
        CriticAction.SUFFICIENT if new.is_clear() else CriticAction.NEXT_HOP
    )
    return {
        **state,
        "chain": chain.model_dump(),
        "requirement_register": new.snapshot(),
        "register_context": context,
        "register_progress": text,
        "register_events": [*(state.get("register_events") or []), event],
    }


def format_progress(changes: list[JsonObject], register: Register) -> str:
    lines = ["[Progress]"]
    for change in changes:
        lines.append(
            f"{change['req_id']}: {change['before']}→{change['after']} "
            + json_text(change["requirement"])
        )
    lines.append(
        "Still unsupported: " + "; ".join(r.req_id + " " + r.text for r in register.open_items())
    )
    return "\n".join(lines)


def snapshot_state(state: AgentState) -> AgentState:
    memory = MemoryState()
    memory.add_evidence([Candidate.model_validate(c) for c in state.get("evidence", [])])
    register = Register.model_validate(state["requirement_register"])
    for item in register.items.values():
        if item.status == RequirementStatus.RESOLVED:
            memory.mark_subquestion_done(item.req_id, item.notes)
    return {**state, "memory_snapshot": memory.to_snapshot()}
