"""Plan coverage bookkeeping: what was planned vs what actually ran (BL-12).

Breadth (sub-question count) and depth (hops) are separate budgets, so a plan
can legitimately end with nodes that never executed. Those nodes must be
reported on the chain rather than silently disappearing.
"""

from __future__ import annotations

from typing import Final

from agentic_graphrag.agent.state import AgentState
from agentic_graphrag.core.reasoning import ReasoningChain

KEY_DROPPED: Final = "dropped_sub_questions"
KEY_UNEXECUTED: Final = "unexecuted_sub_questions"
KEY_SKIPPED: Final = "skipped_sub_questions"


def record_dropped_subquestion(state: AgentState, text: str) -> None:
    """Note a sub-question the breadth budget refused to admit."""
    dropped = list(state.get(KEY_DROPPED) or [])
    if text and text not in dropped:
        dropped.append(text)
    state[KEY_DROPPED] = dropped


def record_skipped(chain: ReasoningChain, skipped: list[dict[str, str]]) -> ReasoningChain:
    """Note plan nodes the executor passed over, with the reason for each."""
    updated = chain.model_copy(deep=True)
    metadata = updated.metadata or {}
    updated.metadata = {**metadata, KEY_SKIPPED: [*(metadata.get(KEY_SKIPPED) or []), *skipped]}
    return updated


def annotate_plan_coverage(chain: ReasoningChain, state: AgentState) -> None:
    """Record planned sub-questions that never ran.

    ``unexecuted`` are nodes that stayed in the plan but never reached the
    executor because the run stopped first; ``dropped`` are nodes the breadth
    budget removed at plan time or refused to insert at critic time. Nodes the
    executor passed over are reported separately under ``skipped``.
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
    accounted = _accounted_texts(chain)
    out: list[str] = []
    for sub_question in state.get("sub_questions") or []:
        text = sub_question.text.strip()
        if text and text not in accounted and text not in out:
            out.append(text)
    return out


def _accounted_texts(chain: ReasoningChain) -> set[str]:
    """Nodes that ran or that the executor deliberately passed over."""
    executed = {(step.sub_question or "").strip() for step in chain.steps}
    skipped = (chain.metadata or {}).get(KEY_SKIPPED) or []
    return executed | {str(item["text"]).strip() for item in skipped}
