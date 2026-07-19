"""P2-AG-04 — guardrail limits load from AppConfig / YAML."""

from __future__ import annotations

from agentic_graphrag.agent.guardrails import GuardrailConfig, Guardrails
from agentic_graphrag.config import AppConfig, GuardrailsConfig, get_config


def test_from_app_config_matches_yaml() -> None:
    cfg = get_config()
    g = GuardrailConfig.from_app_config(cfg)
    assert g.max_hops == cfg.guardrails.max_hops
    assert g.max_llm_calls == cfg.guardrails.max_llm_calls
    assert g.max_tokens == cfg.guardrails.max_tokens_per_query
    assert g.query_timeout_seconds == cfg.guardrails.query_timeout_seconds
    assert g.recursion_limit == cfg.guardrails.recursion_limit


def test_from_guardrails_config_direct() -> None:
    raw = GuardrailsConfig(
        max_hops=3,
        max_llm_calls=7,
        max_tokens_per_query=1000,
        query_timeout_seconds=30,
        recursion_limit=9,
    )
    g = GuardrailConfig.from_app_config(raw)
    assert g.max_hops == 3
    assert g.max_llm_calls == 7
    assert g.max_tokens == 1000
    assert g.recursion_limit == 9


def test_max_hops_override_capped_by_hard_limit() -> None:
    g = GuardrailConfig.from_app_config(AppConfig(), max_hops=100)
    assert g.max_hops == g.hard_max_hops == 20


def test_with_overrides() -> None:
    base = GuardrailConfig.from_app_config(AppConfig())
    o = base.with_overrides(max_hops=2, max_llm_calls=4)
    assert o.max_hops == 2
    assert o.max_llm_calls == 4
    assert o.max_tokens == base.max_tokens


def test_budget_tracker_from_config() -> None:
    g = GuardrailConfig(max_hops=1, max_llm_calls=2, max_tokens=50)
    b = g.budget_tracker()
    assert b.max_llm_calls == 2
    assert b.max_tokens == 50


def test_fallback_summary_includes_paths() -> None:
    g = Guardrails(GuardrailConfig(max_hops=1, max_llm_calls=100, max_tokens=100000))
    g.on_hop_start()
    g.on_hop_start()
    assert g.state.tripped
    text = g.fallback_summary(["A->B", "B->C"])
    assert "max_hops" in text
    assert "A->B" in text
