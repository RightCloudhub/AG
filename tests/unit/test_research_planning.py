"""Required research planning uses the branch allowance and never domain rules."""

import json

import pytest
from test_retrieval_contract import api as api

from agentic_graphrag.agent.blackboard import usage_totals
from agentic_graphrag.agent.planning_policy import PlanningUnavailable
from agentic_graphrag.agent.requirements import Requirement
from agentic_graphrag.agent.research_state import ResearchTask
from agentic_graphrag.agent.researcher import Researcher
from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.app.query_dependencies import query_dependencies
from agentic_graphrag.config_agent import AgentConfig
from agentic_graphrag.core.enums import RetrievalTool
from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.llm.provider import MockLLMProvider


class ToolPlanner(MockLLMProvider):
    def complete(self, messages, **kwargs):
        self.budget.record_call(10, 5)
        return json.dumps({"tool_calls": [{"tool": "fulltext_search", "args": {"query": "Ada"}}]})


@pytest.mark.parametrize("mode", ["open", "schema"])
def test_required_research_plan_is_budgeted_without_heuristics(api, monkeypatch, mode):
    _, svc = api
    knowledge_store(svc).create(
        KnowledgeBase(
            tenant_id="alpha", kb_id="planned", build_mode=mode, rule_planner_profile=None
        )
    )
    selected = select_service(svc, tenant_id="alpha", kb_id="planned")
    selected.allow_llm = True
    root = BudgetTracker(max_llm_calls=10, max_tokens=100_000)
    llm = ToolPlanner(budget=root)
    executor, _ = query_dependencies(selected, root, tenant_id="alpha", llm=llm)
    assert executor.config.llm_only
    monkeypatch.setattr(
        "agentic_graphrag.agent.executor_plan.build_heuristic",
        lambda *_a, **_k: pytest.fail("required planning used domain heuristics"),
    )
    calls = []
    executor.tools.get(RetrievalTool.FULLTEXT_SEARCH).handler = lambda **kw: calls.append(kw) or []
    task = ResearchTask(
        branch_id="branch",
        run_id="run",
        tenant_id="alpha",
        question="Ada",
        requirement=Requirement(req_id="a", text="Ada"),
        round=1,
        token_limit=10000,
        call_limit=2,
        tool_limit=1,
    )
    result = Researcher(executor, llm, config=AgentConfig(mode="multi"), budget=root).retrieve(
        {"task": task, "query": "Ada"}
    )
    assert len(calls) == 1 and calls[0]["tenant_id"] == "alpha"
    assert usage_totals(result["receipts"])["llm_calls"] == root.llm_calls == 1
    assert usage_totals(result["receipts"])["total_tokens"] == root.total_tokens == 15
    assert executor.llm.budget is root
    executor.llm = None
    with pytest.raises(PlanningUnavailable):
        Researcher(executor, llm, config=AgentConfig(mode="multi"), budget=root).retrieve(
            {"task": task, "query": "Ada"}
        )
