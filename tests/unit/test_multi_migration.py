"""Live single mode preserves the explicit register activation switch."""

import pytest
from test_register_runtime import QUESTION, answer, initialization, resolved_update, setup_runtime

from agentic_graphrag.agent.loop import build_graph, invoke_config, run_agentic_query
from agentic_graphrag.agent.options import AgentRunOptions
from agentic_graphrag.agent.state import initial_state
from agentic_graphrag.config_agent import AgentConfig
from agentic_graphrag.core.reasoning import ReasoningChain


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_live_single_respects_register_activation_switch(monkeypatch, direct, enabled):
    executor, llm, guards, _, _ = setup_runtime(
        monkeypatch,
        [
            initialization(),
            resolved_update(),
            {"entailed": True, "reason": "owner"},
            {"entailed": True, "reason": "city"},
            answer(),
        ],
    )
    guards.requirement_register.enabled = enabled

    class LegacyPlannerSelected(Exception):
        pass

    def legacy_planner(*_a, **_k):
        if enabled:
            pytest.fail("enabled register entered old planner")
        raise LegacyPlannerSelected

    monkeypatch.setattr(
        "agentic_graphrag.agent.loop_runtime.plan",
        legacy_planner,
    )
    monkeypatch.setattr(
        "agentic_graphrag.agent.loop_runtime.critique",
        lambda *_a, **_k: pytest.fail("live entered old critic"),
    )

    def invoke():
        if direct:
            state = initial_state(QUESTION, ReasoningChain(question=QUESTION), allow_llm=True)
            return build_graph(executor, llm, guards).invoke(state, invoke_config("direct"))[
                "chain"
            ]
        return run_agentic_query(
            QUESTION,
            executor,
            llm,
            options=AgentRunOptions(
                allow_llm=True, guard_cfg=guards, agent_config=AgentConfig(mode="single")
            ),
        )

    if not enabled:
        with pytest.raises(LegacyPlannerSelected):
            invoke()
        return
    chain = invoke()
    assert "requirement_register" in chain.metadata
    assert chain.status == "answered"
    assert guards.requirement_register.enabled is True
