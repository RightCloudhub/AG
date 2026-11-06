"""Construct the live LLM adapter at the composition boundary."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agentic_graphrag.config import (
    AppConfig,
    Settings,
    get_config,
    get_settings,
    resolve_chat_base_url,
    resolve_embedding_api_key,
    resolve_embedding_base_url,
)

if TYPE_CHECKING:
    from agentic_graphrag.governance.budget import BudgetTracker
    from agentic_graphrag.llm.provider import LLMProvider


def build_llm_provider(
    *,
    budget: BudgetTracker | None = None,
    cache_dir: str | Path | None = None,
    settings: Settings | None = None,
    cfg: AppConfig | None = None,
    tenant_id: str = "default",
    kb_id: str = "default",
) -> LLMProvider:
    """Construct ``LLMProvider`` with chat vs embedding endpoint split."""
    s = settings or get_settings()
    c = cfg or get_config()
    if c.models.roles:
        from agentic_graphrag.llm.role_factory import build_role_provider

        return build_role_provider(
            cfg=c,
            settings=s,
            tenant_id=tenant_id,
            kb_id=kb_id,
            budget=budget,
            cache_dir=cache_dir,
        )
    return _legacy_provider(c, s, budget=budget, cache_dir=cache_dir)


def _legacy_provider(
    c: AppConfig,
    s: Settings,
    *,
    budget: BudgetTracker | None,
    cache_dir: str | Path | None,
) -> LLMProvider:
    from agentic_graphrag.llm.circuit import shared_breakers
    from agentic_graphrag.llm.provider import LLMProvider
    from agentic_graphrag.llm.provider_options import ProviderOptions
    from agentic_graphrag.llm.verifier import warn_same_model

    register = c.guardrails.requirement_register
    if register.enabled and register.verifier_enabled:
        warn_same_model(c.llm.strong_model, register.verifier.model or c.llm.light_model)
    chat_url = resolve_chat_base_url(s)
    embedding_url = resolve_embedding_base_url(s, c)
    return LLMProvider(
        api_key=s.llm_api_key,
        base_url=chat_url,
        strong_model=c.llm.strong_model,
        light_model=c.llm.light_model,
        embedding_model=c.llm.embedding_model,
        embedding_base_url=embedding_url,
        embedding_api_key=resolve_embedding_api_key(s, c),
        temperature=c.llm.temperature,
        timeout_seconds=c.llm.timeout_seconds,
        budget=budget,
        cache_dir=cache_dir,
        circuits=shared_breakers(
            (chat_url, embedding_url, c.llm.strong_model, c.llm.light_model),
            c.llm.resilience.breaker_template(),
        ),
        fallback_to_light=c.llm.resilience.fallback_to_light,
        roles=c.roles,
        options=ProviderOptions(
            auth_required=s.llm_auth_required,
            embedding_auth_required=s.llm_auth_required,
            cache_namespace=c.models.scope_key,
        ),
    )
