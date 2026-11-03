"""Public pipeline resume bypasses routing and preserves completed siblings."""

import json
from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import MemorySaver
from test_multi_runtime import ResearchLLM, setup_run

from agentic_graphrag.agent.options import QueryOptions
from agentic_graphrag.agent.pipeline import run_pipeline
from agentic_graphrag.config_agent import AgentConfig


def test_public_resume_skips_fast_route_and_checks_identity(monkeypatch):
    class FailsOnce(ResearchLLM):
        def __init__(self):
            super().__init__({rid: {"text": f"Who leads Acme {rid}?"} for rid in "ab"})
            self.failed = []

        def complete(self, messages, **kwargs):
            user = messages[-1].content
            if "Research exactly" in user:
                rid = json.loads(user.split("Input:\n", 1)[1])["requirement"]["req_id"]
                if rid == "b" and not self.failed:
                    self.failed.append(True)
                    raise RuntimeError("temporary")
            return super().complete(messages, **kwargs)

    deps, config, _, calls = setup_run(
        monkeypatch, llm=FailsOnce(), config=AgentConfig(mode="multi", max_parallel=3)
    )
    options = QueryOptions(
        allow_llm=True,
        force_agentic=True,
        tenant_id="tenant",
        agent_config=config,
        guard_cfg=deps.guard_cfg,
        checkpointer=MemorySaver(),
        thread_id="run",
    )
    with pytest.raises(RuntimeError, match="temporary"):
        run_pipeline("Who leads Acme?", deps.executor, deps.llm, options=options)
    resume = replace(options, resume=True, force_agentic=False)
    chain = run_pipeline("Who leads Acme?", deps.executor, deps.llm, options=resume)
    assert chain.status == "answered" and sorted(deps.llm.research_calls) == ["a", "b"]
    assert calls == ["tenant", "tenant"]
    with pytest.raises(ValueError, match="checkpoint identity"):
        run_pipeline("Who leads Other?", deps.executor, deps.llm, options=resume)
    with pytest.raises(ValueError, match="existing thread_id"):
        run_pipeline(
            "Who leads Acme?", deps.executor, deps.llm, options=replace(resume, tenant_id="other")
        )
