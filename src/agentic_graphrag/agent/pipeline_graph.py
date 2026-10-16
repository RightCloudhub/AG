"""LangGraph plumbing for the Deep strategy: initial state + stream → events."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from agentic_graphrag.agent.loop_stream_events import events_for_node
from agentic_graphrag.agent.state import initial_state
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ReasoningChain

_STREAM_MODES = ["updates", "values"]


def _initial_state(
    question: str,
    chain: ReasoningChain,
    *,
    allow_llm: bool,
    tenant_id: str = "",
    seed_evidence: list[Candidate] | None = None,
) -> dict[str, Any]:
    """Initial agent state via the single constructor in :mod:`agent.state`."""
    return dict(
        initial_state(
            question,
            chain.model_dump(),
            allow_llm=allow_llm,
            tenant_id=tenant_id,
            evidence=[c.model_dump() for c in seed_evidence or []],
        )
    )


def _stream_graph_updates(
    graph: Any,
    initial: dict[str, Any],
    config: dict[str, Any],
) -> Iterator[tuple[str, Any]]:
    """Yield hop events; return full accumulated state from ``values`` mode."""
    final_state: dict[str, Any] | None = None
    for item in graph.stream(initial, config=config, stream_mode=_STREAM_MODES):
        mode, chunk = _unpack_stream_item(item)
        if mode == "values" and isinstance(chunk, dict):
            final_state = chunk
            continue
        if mode != "updates" or not isinstance(chunk, dict):
            continue
        for node_name, delta in chunk.items():
            if isinstance(delta, dict):
                yield from events_for_node(node_name, delta)
    return final_state  # type: ignore[misc]


def _unpack_stream_item(item: Any) -> tuple[str, Any]:
    """Normalize multi-mode stream items to (mode, chunk)."""
    if isinstance(item, tuple) and len(item) == 2:
        return str(item[0]), item[1]
    # Single-mode fallback: treat bare dict as updates
    return "updates", item
