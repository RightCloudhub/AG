"""Operational snapshots must not reveal other tenants' activity."""

import pytest

from agentic_graphrag.governance.budget import BudgetExceeded
from agentic_graphrag.governance.budget_policy import BudgetLimits, MultiLevelBudget
from agentic_graphrag.observability.metrics import MetricsRegistry, QueryMetrics


def test_metrics_scope_includes_counters_and_legacy_default():
    metrics = MetricsRegistry()
    for tenant, latency in (("alpha", 10), ("beta", 900), ("", 300)):
        metrics.record(QueryMetrics(tenant or "legacy", tenant_id=tenant, latency_ms=latency))
        metrics.record_budget_trip(tenant_id=tenant)
        metrics.record_event_failure(tenant or "legacy", tenant_id=tenant)
    scoped = metrics.summary(tenant_id="alpha")
    assert scoped["count"] == 1
    assert scoped["latency_p95_ms"] == 10
    assert scoped["budget_trips"] == 1
    assert scoped["event_handler_failures"] == {"alpha": 1}
    assert metrics.summary(tenant_id="default")["latency_p95_ms"] == 300
    assert metrics.summary()["count"] == 3


def test_user_budget_keys_cannot_collide_at_tenant_separator():
    budget = MultiLevelBudget(user_limits=BudgetLimits(max_llm_calls=1))
    budget.check_and_reserve(tenant_id="alpha:beta", user_id="user")
    budget.check_and_reserve(tenant_id="alpha", user_id="beta:user")


def test_budget_snapshot_scopes_usage_and_trips():
    budget = MultiLevelBudget(user_limits=BudgetLimits(max_llm_calls=1))
    for tenant in ("alpha", "beta"):
        budget.check_and_reserve(tenant_id=tenant, user_id="user")
        with pytest.raises(BudgetExceeded):
            budget.check_and_reserve(tenant_id=tenant, user_id="user")
    snapshot = budget.snapshot(tenant_id="alpha")
    assert set(snapshot["tenants"]) == {"alpha"}
    assert set(snapshot["users"]) == {"user"}
    assert len(snapshot["trips"]) == 1
    assert snapshot["trips"][0]["tenant_id"] == "alpha"
