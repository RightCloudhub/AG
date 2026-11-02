"""SSE progress event mapping for agent graph node updates."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

from pydantic import TypeAdapter

from agentic_graphrag.agent.state import AgentState
from agentic_graphrag.core.enums import AgentNode, StreamEvent
from agentic_graphrag.core.reasoning import ReasoningChain, ReasoningStep

_METADATA_ADAPTER = TypeAdapter(dict[str, object])

EVENT_FINAL_CHAIN = StreamEvent.FINAL_CHAIN
EVENT_SUB_QUESTION = StreamEvent.SUB_QUESTION
EVENT_HOP_DONE = StreamEvent.HOP_DONE
EVENT_TRIAGE = StreamEvent.TRIAGE
EVENT_THINKING = StreamEvent.THINKING


def events_for_node(node_name: str, delta: AgentState) -> Iterator[tuple[str, dict[str, object]]]:
    """Map one LangGraph node update to zero-or-more SSE (event, payload) pairs."""
    if node_name == AgentNode.PLANNER:
        yield from _events_for_planner(delta)
        return
    last = last_step(delta.get("chain"))
    if last is None:
        return
    hop = last.hop
    if node_name == AgentNode.EXECUTOR:
        yield from _events_for_executor(hop, last)
    elif node_name == AgentNode.CRITIC:
        runtime = delta.get("register_runtime")
        if runtime is not None and runtime.progress:
            yield EVENT_THINKING, {"stage": "progress", "text": runtime.progress}
        yield (
            EVENT_HOP_DONE,
            {
                "hop": hop,
                "conclusion": last.conclusion or "",
                "critic_action": last.critic_action or "",
            },
        )


def fast_path_step_events(steps: list[ReasoningStep]) -> Iterator[tuple[str, dict[str, object]]]:
    """Replay a finished Fast Path chain's steps as progress events.

    Fast Path runs to completion before yielding, so unlike the agentic graph
    there is nothing to stream incrementally — the steps are replayed so that
    clients see the same ``thinking``/``sub_question``/``hop_done`` sequence.
    """
    if not steps:
        return
    yield (
        EVENT_THINKING,
        {
            "stage": "plan",
            "text": f"Fast Path：处理 {len(steps)} 个步骤",
            "detail": "\n".join(
                f"{i}. {s.sub_question}" for i, s in enumerate(steps, 1) if s.sub_question
            ),
        },
    )
    for step in steps:
        yield EVENT_SUB_QUESTION, {"hop": step.hop, "sub_question": step.sub_question}
        yield (
            EVENT_HOP_DONE,
            {
                "hop": step.hop,
                "conclusion": step.conclusion,
                "critic_action": step.critic_action,
            },
        )


def last_step(chain_data: ReasoningChain | None) -> ReasoningStep | None:
    return chain_data.steps[-1] if chain_data is not None and chain_data.steps else None


def _events_for_planner(delta: AgentState) -> Iterator[tuple[str, dict[str, object]]]:
    sqs = delta.get("sub_questions") or []
    texts = [s.text for s in sqs]
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


def _events_for_executor(hop: int, last: ReasoningStep) -> Iterator[tuple[str, dict[str, object]]]:
    sub_q = last.sub_question or ""
    yield EVENT_SUB_QUESTION, {"hop": hop, "sub_question": sub_q}
    tools = [str(t.tool) for t in last.tool_calls if t.tool]
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


def serialize_metadata(metadata: dict[str, object]) -> dict[str, object]:
    """Serialize register audit models when the pipeline returns its API chain."""
    return cast(dict[str, object], _METADATA_ADAPTER.dump_python(metadata, mode="json"))
