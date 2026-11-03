"""Live single mode always uses the register; the legacy loop is offline only."""

import pytest
from test_register_runtime import QUESTION, answer, initialization, resolved_update, setup_runtime

from agentic_graphrag.agent.loop import run_agentic_query
from agentic_graphrag.agent.options import AgentRunOptions
from agentic_graphrag.config_agent import AgentConfig


def test_live_single_ignores_legacy_register_activation_switch(monkeypatch):
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
    guards.requirement_register.enabled = False
    monkeypatch.setattr(
        "agentic_graphrag.agent.loop_runtime.plan",
        lambda *_a, **_k: pytest.fail("live entered old planner"),
    )
    monkeypatch.setattr(
        "agentic_graphrag.agent.loop_runtime.critique",
        lambda *_a, **_k: pytest.fail("live entered old critic"),
    )
    chain = run_agentic_query(
        QUESTION,
        executor,
        llm,
        options=AgentRunOptions(
            allow_llm=True,
            guard_cfg=guards,
            agent_config=AgentConfig(mode="single"),
        ),
    )
    assert "requirement_register" in chain.metadata
    assert chain.status == "answered"
    assert guards.requirement_register.enabled is False
