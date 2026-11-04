"""Completion requests and usage accounting shared by compatible model endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentic_graphrag.core.ports.llm import Message, Tier
from agentic_graphrag.llm import response_cache
from agentic_graphrag.llm.circuit import ROUTE_LIGHT, ROUTE_STRONG
from agentic_graphrag.llm.responses import CompletionResponse, CompletionUsage, completion_text

if TYPE_CHECKING:
    from agentic_graphrag.llm.provider import LLMProvider


def complete(
    provider: LLMProvider,
    messages: list[Message],
    *,
    tier: Tier,
    temperature: float | None,
    response_format: dict[str, Any] | None,
) -> str:
    if provider.budget:
        provider.budget.check()
    tier = provider._effective_tier(tier)
    payload = _payload(
        provider, messages, tier=tier, temperature=temperature, response_format=response_format
    )
    key = response_cache.cache_key(
        "chat",
        {
            "endpoint": provider.base_url,
            "namespace": provider.options.cache_namespace,
            "payload": payload,
        },
    )
    cached = response_cache.read_cache(provider.cache_dir, key)
    if cached is not None:
        usage = CompletionUsage.model_validate(cached.get("usage") or {})
        _record_usage(provider, usage, cached=True)
        return str(cached["content"])
    data = provider._post(
        "/chat/completions",
        payload,
        base_url=provider.base_url,
        api_key=provider.api_key,
        route=ROUTE_STRONG if tier == Tier.STRONG else ROUTE_LIGHT,
    )
    response = CompletionResponse.model_validate(data)
    usage = response.usage or CompletionUsage()
    _record_usage(provider, usage, cached=False)
    content = completion_text(response)
    response_cache.write_cache(
        provider.cache_dir,
        key,
        {
            "content": content,
            "usage": usage.model_dump(),
        },
    )
    return content


def _payload(
    provider: LLMProvider,
    messages: list[Message],
    *,
    tier: Tier,
    temperature: float | None,
    response_format: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": provider.model_for(tier),
        "messages": [{"role": m.role, "content": m.content} for m in messages],
    }
    if provider.options.send_temperature:
        payload["temperature"] = provider.temperature if temperature is None else temperature
    if provider.options.max_output_tokens is not None:
        payload["max_tokens"] = provider.options.max_output_tokens
    if response_format is not None:
        payload["response_format"] = response_format
    return payload


def _record_usage(provider: LLMProvider, usage: CompletionUsage, *, cached: bool) -> None:
    provider.usage["cache_hits" if cached else "chat_calls"] += 1
    if not cached:
        provider.usage["prompt_tokens"] += usage.prompt_tokens
        provider.usage["completion_tokens"] += usage.completion_tokens
    if provider.budget:
        # Preserve deterministic record/replay budget semantics for existing callers.
        provider.budget.record_call(
            usage.prompt_tokens, usage.completion_tokens, check_limits=False
        )


def record_embedding_usage(provider: LLMProvider, data: dict[str, Any], *, kind: str) -> None:
    usage = data.get("usage") or {}
    tokens = int(usage.get("prompt_tokens", 0) or usage.get("total_tokens", 0))
    provider.usage[f"{kind}_calls"] += 1
    provider.usage["prompt_tokens"] += tokens
    if provider.budget:
        provider.budget.record_embedding(tokens, check_limits=False)
