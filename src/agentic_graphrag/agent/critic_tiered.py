"""Tiered critic: deterministic gate first, LLM only when unsure (ARCH_OPTIMIZATION_V2 O-7).

``critic_mode``:

* ``llm``     — V1 behaviour: every hop with evidence asks the LLM critic.
* ``tiered``  — run the offline critic first and accept its verdict when it is
  *decisive*; otherwise ask the LLM (LIGHT tier via the critic prompt).
* ``offline`` — never ask the LLM critic.

A verdict is decisive when the graph evidence yields a clean entity conclusion
for the sub-question (so a placeholder can be materialised or the question
answered) and the offline critic says SUFFICIENT. Text-only evidence, rewrite
suggestions and "need more evidence" always escalate.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentic_graphrag.agent.critic import CriticResult

from agentic_graphrag.agent.critic_offline import extract_entity_conclusion, offline_critique
from agentic_graphrag.agent.options import CritiqueContext
from agentic_graphrag.core.enums import CriticMode

CRITIC_MODES = tuple(CriticMode)
TIER_DETERMINISTIC = "deterministic"
TIER_LLM = "llm"


def normalize_mode(mode: CriticMode | None) -> CriticMode:
    return CriticMode((mode or CriticMode.LLM).strip().lower())


def is_decisive(ctx: CritiqueContext, result: CriticResult) -> bool:
    from agentic_graphrag.agent.critic import CriticAction

    if result.action is not CriticAction.SUFFICIENT:
        return False
    return extract_entity_conclusion(ctx.sub_question, ctx.evidence) is not None


def tiered_critique(
    ctx: CritiqueContext,
    llm_critique: Callable[[CritiqueContext], CriticResult],
) -> tuple[CriticResult, str]:
    """Return ``(result, tier)``; ``tier`` says who decided."""
    offline = offline_critique(ctx)
    if is_decisive(ctx, offline):
        return offline, TIER_DETERMINISTIC
    return llm_critique(ctx), TIER_LLM
