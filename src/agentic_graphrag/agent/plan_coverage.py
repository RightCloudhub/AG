"""Plan coverage bookkeeping: what was planned vs what actually ran (BL-12).

Breadth (sub-question count) and depth (hops) are separate budgets, so a plan
can legitimately end with nodes that never executed. Those nodes must be
reported on the chain rather than silently disappearing.
"""

from __future__ import annotations

from typing import Any

from agentic_graphrag.generation.trace import ReasoningChain

AgentState = dict[str, Any]

KEY_DROPPED = "dropped_sub_questions"
KEY_UNEXECUTED = "unexecuted_sub_questions"


def record_dropped_subquestion(state: AgentState, text: str) -> None:
    """Note a sub-question the breadth budget refused to admit."""
    dropped = list(state.get(KEY_DROPPED) or [])
    if text and text not in dropped:
        dropped.append(text)
    state[KEY_DROPPED] = dropped


def annotate_plan_coverage(chain: ReasoningChain, state: AgentState) -> None:
    """Record planned sub-questions that never ran.

    ``unexecuted`` are nodes that stayed in the plan but never reached the
    executor (hop budget spent); ``dropped`` are nodes the breadth budget
    removed at plan time or refused to insert at critic time.
    """
    unexecuted = _unexecuted(chain, state)
    dropped = [str(t) for t in (state.get(KEY_DROPPED) or []) if str(t).strip()]
    if not unexecuted and not dropped:
        return
    chain.metadata = {
        **(chain.metadata or {}),
        KEY_UNEXECUTED: unexecuted,
        KEY_DROPPED: dropped,
    }


def _unexecuted(chain: ReasoningChain, state: AgentState) -> list[str]:
    executed = {(s.sub_question or "").strip() for s in chain.steps}
    out: list[str] = []
    for raw in state.get("sub_questions") or []:
        text = str((raw or {}).get("text") or "").strip()
        if text and text not in executed and text not in out:
            out.append(text)
    return out
