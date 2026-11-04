"""Role selection, budget reservations and explicit cost attribution."""

from dataclasses import replace

import pytest
from test_multi_runtime import ResearchLLM, setup_run

from agentic_graphrag.agent.blackboard import BudgetEntry, usage_totals
from agentic_graphrag.agent.loop import invoke_config
from agentic_graphrag.agent.multi_graph import build_multi_graph
from agentic_graphrag.agent.multi_merge import merge
from agentic_graphrag.agent.triage_multi import budget_tier, summary_route, tier_guards
from agentic_graphrag.config import AppConfig, Settings
from agentic_graphrag.config_roles import RoleModelConfig, RolesConfig
from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.ports.llm import Message
from agentic_graphrag.governance.budget import BudgetExceeded, BudgetTracker
from agentic_graphrag.llm.factory import build_llm_provider


def roles():
    return RolesConfig(
        **{
            name: RoleModelConfig(
                model=f"model-{name}", input_usd_per_million=2, output_usd_per_million=4
            )
            for name in ("triage", "researcher", "verifier", "writer")
        }
    )


def test_config_factory_and_role_requests_preserve_parent(monkeypatch):
    cfg = AppConfig(roles=roles())
    base = build_llm_provider(settings=Settings(llm_api_key="test"), cfg=cfg)
    requests = []

    def post(_path, payload, **_kwargs):
        requests.append(payload)
        return {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        }

    monkeypatch.setattr(base, "_post", post)
    for role in ("triage", "researcher", "verifier", "writer"):
        budget = BudgetTracker(max_tokens=1000)
        client = base.for_role(role).for_budget(budget, max_output_tokens=50)
        client.complete([Message(role="user", content="hello")])
        assert budget.total_tokens == 3
        assert requests[-1]["model"] == f"model-{role}"
    assert base.strong_model == cfg.llm.strong_model and base.budget is None
    assert base.max_output_tokens is None


def test_model_costs_and_batched_verifier_sum_without_double_counting(monkeypatch):
    llm = ResearchLLM({rid: {"text": f"Who leads Acme {rid}?"} for rid in "abc"})
    llm.roles = roles()
    deps, config, state, _ = setup_run(monkeypatch, llm=llm)
    result = build_multi_graph(deps, config).invoke(
        state, invoke_config("cost", recursion_limit=40)
    )
    ledger = result["budget_ledger"]
    assert usage_totals(ledger)["total_tokens"] == 90
    assert result["chain"].metadata["cost_usd"] == pytest.approx(6 * 40 / 1e6)
    verifier = [e for e in ledger if e.role == "verifier"]
    assert {e.req_id for e in verifier} == set("abc")
    assert sum(e.calls for e in verifier) == 1
    assert sum(e.tokens for e in verifier) == 15
    assert all(e.model == "model-verifier" for e in verifier)
    assert {e.model for e in result["events"]} == {"model-researcher", "model-verifier"}


def test_reject_same_verifier_model_and_unknown_prices(monkeypatch):
    llm = ResearchLLM()
    llm.roles = RolesConfig(verifier=RoleModelConfig(model=llm.strong_model))
    deps, config, _, _ = setup_run(monkeypatch, llm=llm)
    with pytest.raises(ValueError, match="different model"):
        build_multi_graph(deps, config)
    assert RoleModelConfig().cost(10, 5) is None
    assert RoleModelConfig().cost(0, 0) == 0


def test_no_startup_budget_and_cross_tenant_seed(monkeypatch):
    deps, config, state, calls = setup_run(monkeypatch)
    deps = replace(deps, guard_cfg=replace(deps.guard_cfg, max_tokens=0, max_llm_calls=0))
    result = build_multi_graph(deps, config).invoke(state, invoke_config("empty"))
    assert result["register_runtime"].stop == "token_budget"
    assert result["chain"].status.value == "no_answer" and calls == []
    assert usage_totals(result["budget_ledger"])["total_tokens"] == 0
    state["evidence"]["other"] = Candidate(
        id="other",
        source=CandidateSource.FULLTEXT_CHUNK,
        content="private",
        metadata={"tenant_id": "other"},
    )
    with pytest.raises(ValueError, match="tenant"):
        merge(state, rejection_cap=2)


def test_inherited_usage_reduces_bootstrap_allowance(monkeypatch):
    deps, config, state, _ = setup_run(monkeypatch)
    state["budget_ledger"] = [
        BudgetEntry(
            entry_id="entry",
            branch_id="entry",
            run_id="run",
            tenant_id="tenant",
            req_id="entry",
            role="triage",
            calls=2,
            prompt_tokens=99999,
        )
    ]
    result = build_multi_graph(deps, config).invoke(state, invoke_config("prior"))
    assert usage_totals(result["budget_ledger"])["total_tokens"] == 99999


def test_summary_routes_and_tier_caps(monkeypatch):
    deps, config, _, _ = setup_run(monkeypatch)
    assert summary_route("Summarize NovaTech") is not None
    assert budget_tier("Who leads NovaTech?", fast=True) == "small"
    assert budget_tier("Summarize NovaTech") == "summary"
    assert tier_guards(deps.guard_cfg, config, "small").max_tokens == 12000
    assert tier_guards(deps.guard_cfg, config, "summary").max_hops == 3


def test_scoped_embedding_cannot_exceed_remaining_tokens(monkeypatch):
    base = build_llm_provider(settings=Settings(llm_api_key="test"))
    monkeypatch.setattr(base, "_post", lambda *_a, **_k: pytest.fail("network on exhausted budget"))
    client = base.for_budget(BudgetTracker(max_tokens=1), max_output_tokens=1)
    with pytest.raises(BudgetExceeded):
        client.embed("some query")
