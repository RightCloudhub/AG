"""Critic next_hop/rewrite must not skip remaining planned sub-questions."""

from __future__ import annotations

from agentic_graphrag.agent.critic import CriticAction, CriticResult
from agentic_graphrag.agent.guardrails import GuardrailConfig, Guardrails
from agentic_graphrag.agent.loop_handlers import CriticApplyCtx, _append_dynamic_subquestion
from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.planner import SubQuestion
from agentic_graphrag.agent.state import AgentState
from agentic_graphrag.core.reasoning import ReasoningChain


def test_dynamic_sq_inserts_after_current_not_end() -> None:
    sqs = [
        SubQuestion(id="sq1", text="find parent"),
        SubQuestion(id="sq2", text="find ceo of parent"),
        SubQuestion(id="sq3", text="confirm"),
    ]
    memory = MemoryState()
    guards = Guardrails(GuardrailConfig())
    ctx = CriticApplyCtx(
        state={"chain": ReasoningChain(question="q")},
        result=CriticResult(
            action=CriticAction.NEXT_HOP,
            new_sub_question="what is parent of X?",
            rationale="need parent first",
        ),
        sq=SubQuestion(id="sq1", text="find parent"),
        sq_text="find parent",
        sq_id="sq1",
        idx=0,
        remaining=2,
        memory=memory,
        guards=guards,
        guard_cfg=GuardrailConfig(),
    )
    new_state: AgentState = {}
    _append_dynamic_subquestion(new_state, ctx, sqs)
    out = new_state["sub_questions"]
    assert len(out) == 4
    # Inserted immediately after current (idx 0) → position 1
    assert out[1].text == "what is parent of X?"
    assert out[1].id.startswith("sq_dyn_")
    # Original tail still present
    assert out[2].text == "find ceo of parent"
    assert out[3].text == "confirm"
    assert new_state["current_index"] == 1
