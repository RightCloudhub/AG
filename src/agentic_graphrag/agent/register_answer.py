"""Bounded answers and complete register audit metadata, including partial runs."""

from __future__ import annotations

from agentic_graphrag.agent.register_llm import json_text
from agentic_graphrag.agent.register_progress import context_size
from agentic_graphrag.agent.requirements import VERIFIER_RETIRE_REASON, Register
from agentic_graphrag.agent.state import AgentState, active_register
from agentic_graphrag.config import load_prompt
from agentic_graphrag.core.enums import PromptName, RequirementStatus
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.reasoning import QueryStatus, ReasoningChain
from agentic_graphrag.generation.answer import generate_answer
from agentic_graphrag.governance.budget import BudgetExceeded


def answer_from_register(
    state: AgentState,
    llm: LLMClient | None,
    *,
    allow_llm: bool,
    context_limit: int,
    offline_fallback: bool = True,
) -> ReasoningChain:
    register = active_register(state)
    runtime = state["register_runtime"]
    chain = state["chain"].model_copy(deep=True)
    evidence = list(state.get("evidence", {}).values())
    missing = _missing_requirements(register, runtime.rejections)
    if missing and runtime.answer_policy == "abstain":
        chain.honest_fallback("unresolved information needs")
        return annotate_register(chain, state)
    conclusions = (
        load_prompt(PromptName.REGISTER_FORCE_ANSWER) + "\n" + json_text(register.snapshot())
    )
    prompt_bound = (
        conclusions
        + load_prompt(PromptName.ANSWER)
        + json_text([c.model_dump(mode="json") for c in evidence])
    )
    allow_llm = allow_llm and context_size([], prompt_bound) <= context_limit
    if not allow_llm and not offline_fallback:
        chain.honest_fallback("writer budget exhausted")
        return annotate_register(chain, state)
    try:
        chain = generate_answer(
            chain,
            evidence,
            llm,
            conclusions=conclusions,
            guardrail_status=runtime.stop or "register clear",
            allow_llm=allow_llm,
            report_evidence_gaps=False,
        )
    except BudgetExceeded:
        if offline_fallback:
            chain = generate_answer(chain, evidence, None, conclusions="", allow_llm=False)
        else:
            chain.honest_fallback("writer budget exhausted")
    return annotate_register(chain, state)


def annotate_register(chain: ReasoningChain, state: AgentState) -> ReasoningChain:
    register = active_register(state)
    runtime = state["register_runtime"]
    missing = _missing_requirements(register, runtime.rejections)
    if missing:
        if runtime.answer_policy == "abstain":
            chain.honest_fallback("unresolved information needs")
        chain.missing_info = list(dict.fromkeys([*chain.missing_info, *missing]))
        if chain.status == QueryStatus.ANSWERED:
            chain.status = QueryStatus.PARTIAL
        chain.answer += "\n仍未支撑的项：" + "；".join(missing)
    chain.metadata = {
        **chain.metadata,
        "requirement_register": register,
        "register_events": runtime.events,
        "register_rejections": dict(runtime.rejections),
        "register_stop": runtime.stop or "clear",
        "register_rounds": state.get("hop", 0),
        "register_retrieved_ids": list(state.get("evidence", {})),
        "register_evidence": list(state.get("evidence", {}).values()),
        "register_context_byte_bound": context_size(runtime.context),
    }
    return chain


def _missing_requirements(register: Register, rejections: dict[str, int]) -> list[str]:
    # Rejection history survives later evidence-based retirement; check the cause.
    return [
        r.text
        for r in register.items.values()
        if r.status != RequirementStatus.RESOLVED
        and (
            r.status != RequirementStatus.RETIRED
            or r.retire_reason == VERIFIER_RETIRE_REASON.format(count=rejections.get(r.req_id, 0))
        )
    ]
