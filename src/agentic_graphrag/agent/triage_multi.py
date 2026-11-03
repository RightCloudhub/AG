"""Live-only routing metadata and budget tiers; offline rules are unchanged."""

import re
from dataclasses import replace

from agentic_graphrag.agent.blackboard import BudgetTier
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.triage import Route, TriageResult
from agentic_graphrag.config_agent import AgentConfig

_SUMMARY = re.compile(r"\b(summar(?:y|ize|ise)|overview|synthesis)\b|总结|概述|综述", re.I)


def forced_route(*, force_agentic: bool) -> TriageResult:
    rule = "force_agentic" if force_agentic else "triage_disabled"
    return TriageResult(
        route=Route.AGENTIC, rationale=rule, estimated_hops=2, confidence=1.0, rule_hit=rule
    )


def summary_route(question: str) -> TriageResult | None:
    if not _SUMMARY.search(question):
        return None
    return TriageResult(
        route=Route.AGENTIC, rationale="summary", rule_hit="summary", estimated_hops=3
    )


def budget_tier(question: str, *, fast: bool = False) -> BudgetTier:
    if summary_route(question):
        return "summary"
    return "small" if fast else "standard"


def live_metadata(question: str, decision: TriageResult) -> dict[str, object]:
    tier = budget_tier(question, fast=decision.route == Route.FAST_PATH)
    kind = {"small": "single_hop", "standard": "multi_hop", "summary": "summary"}[tier]
    return {**decision.model_dump(mode="json"), "budget_tier": tier, "question_kind": kind}


def tier_guards(guards: GuardrailConfig, config: AgentConfig, tier: BudgetTier) -> GuardrailConfig:
    limits = getattr(config.budget_tiers, tier)
    return replace(
        guards,
        max_tokens=min(guards.max_tokens, limits.tokens),
        max_llm_calls=min(guards.max_llm_calls, limits.calls),
        max_hops=min(guards.max_hops, limits.rounds),
    )
