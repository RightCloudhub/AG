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
) -> LLMProvider:
    """Construct ``LLMProvider`` with chat vs embedding endpoint split."""
    from agentic_graphrag.llm.provider import LLMProvider

    s = settings or get_settings()
    c = cfg or get_config()
    return LLMProvider(
        api_key=s.llm_api_key,
        base_url=resolve_chat_base_url(s),
        strong_model=c.llm.strong_model,
        light_model=c.llm.light_model,
        embedding_model=c.llm.embedding_model,
        embedding_base_url=resolve_embedding_base_url(s, c),
        embedding_api_key=resolve_embedding_api_key(s, c),
        temperature=c.llm.temperature,
        timeout_seconds=c.llm.timeout_seconds,
        budget=budget,
        cache_dir=cache_dir,
        circuit=c.llm.resilience.breaker_template(),
        fallback_to_light=c.llm.resilience.fallback_to_light,
    )
