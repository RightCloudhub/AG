"""Approved progress messages, verification events and context accounting."""

from __future__ import annotations

from agentic_graphrag.agent.register_invariants import schedule
from agentic_graphrag.agent.register_llm import initial_context, json_text, verify
from agentic_graphrag.agent.requirements import Register, Requirement, diff
from agentic_graphrag.agent.state import AgentState
from agentic_graphrag.config import load_prompt
from agentic_graphrag.core.enums import MessageRole, PromptName, RequirementStatus
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.governance.budget import BudgetExceeded


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
) -> list[JsonObject]:
    events = []
    for rid, item in new.items.items():
        previous = old.items.get(rid)
        if item.status != RequirementStatus.RESOLVED or (
            previous and previous.snapshot() == item.snapshot()
        ):
            continue
        report = _verify_one(item, evidence, llm, context_limit=context_limit)
        events.append({"req_id": rid, "passages": sorted(item.passages), **report})
        if not report["entailed"]:
            item.status = RequirementStatus.PENDING
    schedule(new)
    return events


def _verify_one(
    item: Requirement, evidence: dict[str, Candidate], llm: LLMClient, *, context_limit: int
) -> JsonObject:
    supporting = [evidence[i].model_dump(mode="json") for i in sorted(item.passages)]
    if (
        context_size(
            [], json_text(supporting) + item.text + load_prompt(PromptName.REGISTER_VERIFY)
        )
        > context_limit
    ):
        return {"entailed": False, "reason": "verifier context limit", "error": "context_limit"}
    try:
        return verify(item, evidence, llm).model_dump()
    except BudgetExceeded:
        raise
    except Exception as exc:
        return {
            "entailed": False,
            "reason": "verifier could not establish sufficiency",
            "error": type(exc).__name__,
        }


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
    event = {
        "type": "progress",
        "round": state.get("hop", 0),
        "changes": changes,
        "verifier": verifier,
        "new_passages": state.get("register_fresh", []),
    }
    return {
        **state,
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
