"""OpenAI-compatible LLM provider with dual tiers and embedding support."""

from __future__ import annotations

import hashlib
import json
from copy import copy
from pathlib import Path
from typing import Any

import httpx as httpx  # Compatibility for provider-boundary HTTP stubs.

from agentic_graphrag.core.ports.llm import Message, Tier
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.llm import response_cache
from agentic_graphrag.llm.circuit import (
    ROUTE_EMBED,
    ROUTE_LIGHT,
    ROUTE_STRONG,
    CircuitBreaker,
    CircuitBreakerSet,
)
from agentic_graphrag.llm.embedding_response import embedding_rows
from agentic_graphrag.llm.http_transport import ProviderHttpPool
from agentic_graphrag.llm.provider_completion import complete, record_embedding_usage
from agentic_graphrag.llm.provider_options import ProviderOptions
from agentic_graphrag.llm.verifier import warn_same_model


class LLMProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        *,
        strong_model: str = "gpt-4.1",
        light_model: str = "gpt-4.1-mini",
        embedding_model: str = "text-embedding-3-small",
        embedding_base_url: str | None = None,
        embedding_api_key: str | None = None,
        temperature: float = 0.0,
        timeout_seconds: float = 60.0,
        budget: BudgetTracker | None = None,
        cache_dir: str | Path | None = None,
        circuit: CircuitBreaker | None = None,
        fallback_to_light: bool = False,
        options: ProviderOptions | None = None,
    ) -> None:
        self.options = options or ProviderOptions()
        self.http_pool = ProviderHttpPool()
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        # Separate embedding endpoint (multi-provider); fall back to chat endpoint.
        emb_url = (embedding_base_url or "").strip() or self.base_url
        self.embedding_base_url = emb_url.rstrip("/")
        emb_key = embedding_api_key if embedding_api_key not in (None, "") else api_key
        self.embedding_api_key = emb_key
        self.strong_model = strong_model
        self.light_model = light_model
        self.embedding_model = embedding_model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.budget = budget
        self.usage = dict.fromkeys(
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
        self.cache_dir = response_cache.directory(cache_dir)
        # One breaker per route (O-8); ``circuit`` is the template they clone.
        self.circuits = CircuitBreakerSet(circuit)
        self.fallback_to_light = fallback_to_light
        self.degradations = 0

    def model_for(self, tier: Tier) -> str:
        return self.strong_model if tier == Tier.STRONG else self.light_model

    def format_for_schema(self, schema: dict[str, Any]) -> dict[str, Any] | None:
        from agentic_graphrag.llm.output_format import output_format

        return output_format(self.options.structured_output, schema)

    def for_verifier(self, *, model: str | None = None) -> LLMProvider:
        """Select a judge without mutating the proposer or dropping its budget/stub."""
        selected = model or self.light_model
        warn_same_model(self.strong_model, selected)
        verifier = copy(self)
        verifier.strong_model = verifier.light_model = selected
        verifier.fallback_to_light = False
        verifier.circuits = CircuitBreakerSet(self.circuits.template)
        return verifier

    @property
    def circuit(self) -> CircuitBreaker:
        """Breaker a STRONG call would go through now (LIGHT when degrading)."""
        strong = self.circuits.route(ROUTE_STRONG)
        light = self.circuits.route(ROUTE_LIGHT)
        if not strong.allow() and self.fallback_to_light and light.allow():
            return light
        return strong

    def _effective_tier(self, tier: Tier) -> Tier:
        if tier != Tier.STRONG or not self.fallback_to_light:
            return tier
        if self.circuits.route(ROUTE_STRONG).allow():
            return tier
        if not self.circuits.route(ROUTE_LIGHT).allow():
            return tier
        self.degradations += 1
        return Tier.LIGHT

    def complete(
        self,
        messages: list[Message],
        *,
        tier: Tier = Tier.STRONG,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        return complete(
            self, messages, tier=tier, temperature=temperature, response_format=response_format
        )

    def embed(self, text: str) -> list[float]:
        return self.embed_for_model(text, self.embedding_model)

    def embed_for_model(self, text: str, model: str) -> list[float]:
        payload = {"model": model or self.embedding_model, "input": text}
        # Include endpoint in cache key so multi-provider caches do not collide.
        cache_key = response_cache.cache_key(
            "embed",
            {
                "base_url": self.embedding_base_url,
                "namespace": self.options.cache_namespace,
                "payload": payload,
            },
        )
        cached = response_cache.read_cache(self.cache_dir, cache_key)
        if cached is not None:
            self.usage["cache_hits"] += 1
            return embedding_rows(
                {"data": [{"embedding": cached["embedding"]}]},
                count=1,
                dimensions=self.options.embedding_dimensions,
            )[0]

        data = self._post(
            "/embeddings",
            payload,
            base_url=self.embedding_base_url,
            api_key=self.embedding_api_key,
            key_env="LLM_EMBEDDING_API_KEY (or LLM_API_KEY)",
            route=ROUTE_EMBED,
        )
        record_embedding_usage(self, data, kind="embedding")
        embedding = embedding_rows(data, count=1, dimensions=self.options.embedding_dimensions)[0]
        response_cache.write_cache(self.cache_dir, cache_key, {"embedding": embedding})
        return list(embedding)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": self.embedding_model, "input": texts}
        data = self._post(
            "/embeddings",
            payload,
            base_url=self.embedding_base_url,
            api_key=self.embedding_api_key,
            route=ROUTE_EMBED,
        )
        record_embedding_usage(self, data, kind="embedding")
        return embedding_rows(data, count=len(texts), dimensions=self.options.embedding_dimensions)

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        base_url: str,
        api_key: str,
        key_env: str = "LLM_API_KEY",
        route: str = ROUTE_STRONG,
    ) -> dict[str, Any]:
        from agentic_graphrag.governance.execution import check_execution

        check_execution()
        if self.budget and self.budget.total_tokens >= self.budget.max_tokens:
            self.budget.check()
        required = (
            self.options.embedding_auth_required
            if route == ROUTE_EMBED
            else self.options.auth_required
        )
        if required and not api_key:
            raise RuntimeError(
                f"{key_env} is not set; configure credentials or an explicit keyless provider"
            )
        breaker = self.circuits.route(route)
        if not breaker.allow():
            raise RuntimeError(f"LLM circuit open ({route}): too many failures")
        try:
            data = self.http_pool.post(
                path, payload, base_url=base_url, api_key=api_key, timeout=self.timeout_seconds
            )
        except Exception:
            breaker.record_failure()
            raise
        breaker.record_success()
        return data

    def close(self) -> None:
        self.http_pool.close()


class MockLLMProvider(LLMProvider):
    """Deterministic stub for unit tests — no network."""

    # A canned stub cannot judge entailment; the answer gate skips the judge.
    supports_entailment = False

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        embedding_dim: int = 8,
        budget: BudgetTracker | None = None,
    ) -> None:
        super().__init__(api_key="mock", budget=budget)
        self.responses = responses or {}
        self.embedding_dim = embedding_dim
        self.calls: list[list[Message]] = []

    def complete(
        self,
        messages: list[Message],
        *,
        tier: Tier = Tier.STRONG,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        del temperature, response_format
        if self.budget:
            self.budget.record_call(prompt_tokens=10, completion_tokens=5)
        self.calls.append(messages)
        blob = " ".join(m.content for m in messages)
        for key, value in self.responses.items():
            if key in blob:
                return value
        return json.dumps({"ok": True, "echo": blob[:200]})

    def embed(self, text: str) -> list[float]:
        # Stable pseudo-embedding from text hash.
        h = hashlib.sha256(text.encode("utf-8")).digest()
        vals = [((h[i % len(h)] / 255.0) * 2 - 1) for i in range(self.embedding_dim)]
        return vals

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]

    def embed_for_model(self, text: str, model: str) -> list[float]:
        return self.embed(text)
