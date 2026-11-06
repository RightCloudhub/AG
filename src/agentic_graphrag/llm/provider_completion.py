"""Completion requests and usage accounting shared by compatible model endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from agentic_graphrag.core.ports.llm import Message, Tier
from agentic_graphrag.llm import response_cache
from agentic_graphrag.llm.responses import CompletionUsage
from agentic_graphrag.llm.scoped import completion_payload

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
        provider.usage["cache_hits"] += 1
        if provider.budget:
            provider.budget.record_call(usage.prompt_tokens, usage.completion_tokens)
        return str(cached["content"])
    content, raw_usage = provider._metered_chat(payload, tier)
    usage = CompletionUsage.model_validate(raw_usage)
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
    limits = [
        limit
        for limit in (provider.max_output_tokens, provider.options.max_output_tokens)
        if limit is not None
    ]
    payload = completion_payload(
        messages,
        provider.model_for(tier),
        temperature=provider.temperature if temperature is None else temperature,
        response_format=response_format,
        max_output_tokens=min(limits) if limits else None,
        budget=provider.budget,
    )
    if not provider.options.send_temperature:
        payload.pop("temperature", None)
    return payload


def record_chat_usage(provider: LLMProvider, data: dict[str, Any]) -> None:
    """Retain profile usage even when a completed reply cannot be consumed."""
    provider.usage["chat_calls"] += 1
    try:
        usage = CompletionUsage.model_validate(data.get("usage") or {})
    except ValidationError:
        return  # Settlement reports invalid usage; unknown token counts stay zero.
    provider.usage["prompt_tokens"] += usage.prompt_tokens
    provider.usage["completion_tokens"] += usage.completion_tokens


def record_embedding_usage(provider: LLMProvider, data: dict[str, Any], *, kind: str) -> None:
    tokens = 0
    provider.usage[f"{kind}_calls"] += 1
    try:
        usage = data.get("usage") or {}
        tokens = int(usage.get("prompt_tokens", 0) or usage.get("total_tokens", 0))
    finally:
        provider.usage["prompt_tokens"] += max(0, tokens)
        if provider.budget:
            provider.budget.record_embedding(tokens)


def empty_usage() -> dict[str, int]:
    return dict.fromkeys(
        (
            "chat_calls",
            "embedding_calls",
            "rerank_calls",
            "cache_hits",
            "prompt_tokens",
            "completion_tokens",
        ),
        0,
    )
