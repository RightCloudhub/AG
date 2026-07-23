"""SSE progress event mapping for agent graph node updates."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

EVENT_FINAL_CHAIN = "__final_chain__"
EVENT_SUB_QUESTION = "sub_question"
EVENT_HOP_DONE = "hop_done"
EVENT_TRIAGE = "triage"
EVENT_THINKING = "thinking"


def events_for_node(node_name: str, delta: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Map one LangGraph node update to zero-or-more SSE (event, payload) pairs."""
    if node_name == "planner":
        yield from _events_for_planner(delta)
        return
    last = last_step(delta.get("chain"))
    if last is None:
        return
    hop = last.get("hop", 0)
    if node_name == "executor":
        yield from _events_for_executor(hop, last)
    elif node_name == "critic":
        yield (
            EVENT_HOP_DONE,
            {
                "hop": hop,
                "conclusion": last.get("conclusion") or "",
                "critic_action": last.get("critic_action") or "",
            },
        )


def last_step(chain_data: Any) -> dict[str, Any] | None:
    if not isinstance(chain_data, dict):
        return None
    steps = chain_data.get("steps") or []
    if not steps or not isinstance(steps[-1], dict):
        return None
    return steps[-1]


def _events_for_planner(delta: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    sqs = delta.get("sub_questions") or []
    texts = [str(s.get("text") or "") for s in sqs if isinstance(s, dict)]
    texts = [t for t in texts if t]
    lines = [f"{i}. {t}" for i, t in enumerate(texts, 1)]
    yield (
        EVENT_THINKING,
        {
            "stage": "plan",
            "text": f"规划拆解为 {len(texts)} 个子问题",
            "detail": "\n".join(lines),
        },
    )


def _events_for_executor(hop: Any, last: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    sub_q = last.get("sub_question") or ""
    yield EVENT_SUB_QUESTION, {"hop": hop, "sub_question": sub_q}
    tools = [
        str(t.get("tool") or "")
        for t in (last.get("tool_calls") or [])
        if isinstance(t, dict) and t.get("tool")
    ]
    if not tools:
        return
    yield (
        EVENT_THINKING,
        {
            "stage": "retrieve",
            "text": f"hop {hop}: 检索 {', '.join(tools)}",
            "detail": sub_q,
        },
    )
