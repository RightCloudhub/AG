"""Branch-aware SSE frames using existing event names for old clients."""

from collections.abc import Iterator

from agentic_graphrag.agent.state import AgentState


def multi_events(node: str, delta: AgentState) -> Iterator[tuple[str, dict[str, object]]]:
    if node == "bootstrap":
        yield "thinking", {"stage": "plan", "text": "Research requirements initialized"}
    if node not in {"researcher", "verify"}:
        return
    for event in delta.get("events", []):
        payload: dict[str, object] = {
            "branch_id": event.branch_id,
            "req_id": event.req_id,
            "hop": event.round,
            "role": event.role,
            "model": event.model,
            "tokens": event.tokens,
            "outcome": event.outcome,
        }
        if node == "researcher":
            yield "thinking", {**payload, "stage": "progress", "text": event.outcome}
            yield "hop_done", {**payload, "conclusion": "", "critic_action": "next_hop"}
        else:
            yield (
                "hop_done",
                {
                    **payload,
                    "conclusion": event.detail,
                    "critic_action": "sufficient" if event.outcome == "accepted" else "next_hop",
                },
            )
