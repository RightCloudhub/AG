"""Bounded answers and complete register audit metadata, including partial runs."""

from __future__ import annotations

from agentic_graphrag.agent.register_llm import json_text
from agentic_graphrag.agent.register_progress import context_size
from agentic_graphrag.agent.requirements import Register
from agentic_graphrag.agent.state import AgentState
from agentic_graphrag.config import load_prompt
from agentic_graphrag.core.enums import PromptName
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.reasoning import QueryStatus, ReasoningChain
from agentic_graphrag.generation.answer import generate_answer
from agentic_graphrag.governance.budget import BudgetExceeded


def answer_from_register(
    state: AgentState, llm: LLMClient | None, *, allow_llm: bool, context_limit: int
) -> ReasoningChain:
    register = Register.model_validate(state["requirement_register"])
    chain = ReasoningChain.model_validate(state["chain"])
    evidence = [Candidate.model_validate(c) for c in state.get("evidence", [])]
    if register.open_items() and state.get("register_answer_policy") == "abstain":
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
    try:
        chain = generate_answer(
            chain,
            evidence,
            llm,
            conclusions=conclusions,
            guardrail_status=state.get("register_stop") or "register clear",
            allow_llm=allow_llm,
            report_evidence_gaps=False,
        )
    except BudgetExceeded:
        chain = generate_answer(chain, evidence, None, conclusions="", allow_llm=False)
    return annotate_register(chain, state)


def annotate_register(chain: ReasoningChain, state: AgentState) -> ReasoningChain:
    register = Register.model_validate(state["requirement_register"])
    missing = [r.text for r in register.open_items()]
    if missing:
        if state.get("register_answer_policy") == "abstain":
            chain.honest_fallback("unresolved information needs")
        chain.missing_info = list(dict.fromkeys([*chain.missing_info, *missing]))
        if chain.status == QueryStatus.ANSWERED:
            chain.status = QueryStatus.PARTIAL
        chain.answer += "\n仍未支撑的项：" + "；".join(missing)
    chain.metadata = {
        **chain.metadata,
        "requirement_register": register.snapshot(),
        "register_events": state.get("register_events", []),
        "register_stop": state.get("register_stop") or "clear",
        "register_rounds": state.get("hop", 0),
        "register_retrieved_ids": [c["id"] for c in state.get("evidence", [])],
        "register_evidence": state.get("evidence", []),
        "register_context_byte_bound": context_size(state.get("register_context", [])),
    }
    return chain
