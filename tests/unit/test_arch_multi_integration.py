"""Contracts where the architecture and multi-agent branches meet."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError
from test_multi_runtime import setup_run

from agentic_graphrag.agent.loop import run_query
from agentic_graphrag.agent.options import QueryOptions
from agentic_graphrag.config import AppConfig, Settings
from agentic_graphrag.core.enums import LLMRole
from agentic_graphrag.core.ports.llm import Message, Tier
from agentic_graphrag.governance.budget import BudgetExceeded, BudgetTracker, role_scope
from agentic_graphrag.llm.factory import build_llm_provider
from agentic_graphrag.llm.provider import LLMProvider


def test_branch_calls_share_the_root_limit_atomically():
    root = BudgetTracker(max_llm_calls=1)
    branches = [BudgetTracker(parent=root) for _ in range(3)]
    start = Barrier(3)

    def attempt(branch):
        start.wait(timeout=5)
        try:
            branch.reserve_call()
        except BudgetExceeded:
            return False
        branch.settle_call(7, 3)
        return True

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert sum(pool.map(attempt, branches)) == 1
    assert root.llm_calls == sum(branch.llm_calls for branch in branches) == 1
    assert root.total_tokens == 10


def test_scoped_failure_embedding_and_role_usage_reach_root(monkeypatch):
    root = BudgetTracker(max_llm_calls=1)
    branch = BudgetTracker(parent=root)
    llm = LLMProvider(api_key="stub", budget=branch)
    monkeypatch.setattr(
        llm,
        "_post",
        lambda *_a, **_k: {
            "choices": [],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        },
    )
    with role_scope(LLMRole.REGISTER_UPDATE), pytest.raises(ValidationError):
        llm.complete([Message(role="user", content="q")])
    branch.record_embedding(4)
    branch.record_remote(12, failed=True)
    assert root.snapshot() == branch.snapshot()
    assert root.total_tokens == 14 and root.embed_calls == 1
    assert root.role_usage() == branch.role_usage()
    assert root.role_usage()["register_update"]["parse_failures"] == 1
    with pytest.raises(BudgetExceeded):
        BudgetTracker(parent=root).reserve_call()


def test_failed_send_returns_both_root_and_branch_reservations(monkeypatch):
    root = BudgetTracker(max_llm_calls=1)
    branch = BudgetTracker(parent=root)
    llm = LLMProvider(api_key="stub", budget=branch)

    def failed(*_a, **_k):
        raise ConnectionError("unavailable")

    monkeypatch.setattr(llm, "_post", failed)
    with pytest.raises(ConnectionError):
        llm.complete([Message(role="user", content="q")])
    assert root.llm_calls == branch.llm_calls == 0


def test_role_clients_share_circuit_state_across_queries_and_roles():
    cfg = AppConfig()
    cfg.llm.resilience.failure_threshold = 1
    settings = Settings(llm_api_key="stub")
    first = build_llm_provider(cfg=cfg, settings=settings).for_role("researcher")
    second = build_llm_provider(cfg=cfg, settings=settings)
    first.circuit_for(Tier.STRONG).record_failure()
    assert not second.for_role("researcher").circuit_for(Tier.STRONG).allow()
    assert not second.for_role("writer").circuit_for(Tier.STRONG).allow()
    assert second.for_role("verifier").circuit_for(Tier.STRONG).allow()


def test_multi_preserves_entry_embeddings_and_operation_metrics(monkeypatch):
    deps, config, _, _ = setup_run(monkeypatch)
    root = BudgetTracker(max_llm_calls=16)
    with role_scope(LLMRole.TRIAGE):
        root.record_call(7, 3)
    root.record_embedding(3)
    chain = run_query(
        "Who leads Acme?",
        deps.executor,
        deps.llm,
        options=QueryOptions(
            allow_llm=True,
            force_agentic=True,
            guard_cfg=deps.guard_cfg,
            budget=root,
            tenant_id="tenant",
            agent_config=config,
        ),
    )
    assert root.llm_calls == chain.cost.llm_calls == 5
    assert root.total_tokens == chain.cost.tokens == 73
    assert root.embed_calls == chain.cost.embed_calls == 1
    assert chain.metadata["llm_by_role"] == root.role_usage()
    assert root.role_usage()["register_update"]["calls"] == 2
    assert root.role_usage()["register_verify"]["calls"] == 1
    assert "unknown" not in root.role_usage()


def test_single_entry_keeps_original_client_and_limits(monkeypatch):
    from agentic_graphrag.agent.pipeline import _StreamCtx
    from agentic_graphrag.agent.pipeline_usage import entry_client, fast_limit

    deps, config, _, _ = setup_run(monkeypatch)
    root = BudgetTracker(max_llm_calls=16, max_tokens=50000)
    options = QueryOptions(
        allow_llm=True, budget=root, agent_config=config.model_copy(update={"mode": "single"})
    )
    from agentic_graphrag.agent.loop import resolved_run_opts

    ctx = _StreamCtx("q", deps.executor, deps.llm, options, resolved_run_opts(options))
    assert entry_client(ctx, "writer") is deps.llm
    with fast_limit(ctx):
        assert (root.max_llm_calls, root.max_tokens) == (16, 50000)
