"""OpenAI-compatible LLM provider with dual tiers and embedding support."""

from __future__ import annotations

from copy import copy
from pathlib import Path
from typing import Any

import httpx

from agentic_graphrag.config_roles import ModelRole, RoleModelConfig, RolesConfig
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
from agentic_graphrag.llm.responses import JSON_OBJECT, CompletionResponse, CompletionUsage
from agentic_graphrag.llm.roles import select_role
from agentic_graphrag.llm.scoped import check_embedding_budget, completion_payload
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
        roles: RolesConfig | None = None,
    ) -> None:
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
        self.max_output_tokens: int | None = None
        self.roles = roles or RolesConfig()
        self.role_config: RoleModelConfig | None = None
        self.cache_dir = Path(cache_dir) if cache_dir else None
        # One breaker per route (O-8); ``circuit`` is the template they clone.
        self.circuits = CircuitBreakerSet(circuit)
        self.fallback_to_light = fallback_to_light
        self.degradations = 0
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def model_for(self, tier: Tier) -> str:
        return self.strong_model if tier == Tier.STRONG else self.light_model

    def for_budget(self, budget: BudgetTracker, *, max_output_tokens: int) -> LLMProvider:
        client = copy(self)
        client.budget = budget
        client.max_output_tokens = max_output_tokens
        return client

    def for_role(self, role: ModelRole) -> LLMProvider:
        return select_role(self, role)

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
        if self.budget:
            self.budget.check()

        tier = self._effective_tier(tier)
        payload = completion_payload(
            messages,
            self.model_for(tier),
            temperature=self.temperature if temperature is None else temperature,
            response_format=response_format,
            max_output_tokens=self.max_output_tokens,
            budget=self.budget,
        )

        cache_key = response_cache.cache_key("chat", payload)
        cached = response_cache.read_cache(self.cache_dir, cache_key)
        if cached is not None:
            self._record_usage(cached.get("usage") or {})
            return str(cached["content"])

        data = self._post(
            "/chat/completions",
            payload,
            base_url=self.base_url,
            api_key=self.api_key,
            key_env="LLM_API_KEY",
            route=ROUTE_STRONG if tier == Tier.STRONG else ROUTE_LIGHT,
        )
        response = CompletionResponse.model_validate(data)
        content = response.choices[0].message.content
        usage = (response.usage or CompletionUsage()).model_dump()
        self._record_usage(usage)
        response_cache.write_cache(
            self.cache_dir,
            cache_key,
            {"content": content, "usage": usage},
        )
        return content

    def _record_usage(self, usage: dict[str, Any]) -> None:
        if self.budget:
            self.budget.record_call(
                int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
            )

    def embed(self, text: str) -> list[float]:
        return self.embed_for_model(text, self.embedding_model)

    def embed_for_model(self, text: str, model: str) -> list[float]:
        if self.max_output_tokens is not None:
            check_embedding_budget(text, self.budget)
        payload = {"model": model or self.embedding_model, "input": text}
        # Include endpoint in cache key so multi-provider caches do not collide.
        cache_key = response_cache.cache_key(
            "embed",
            {"base_url": self.embedding_base_url, "payload": payload},
        )
        cached = response_cache.read_cache(self.cache_dir, cache_key)
        if cached is not None:
            return list(cached["embedding"])

        data = self._post(
            "/embeddings",
            payload,
            base_url=self.embedding_base_url,
            api_key=self.embedding_api_key,
            key_env="LLM_EMBEDDING_API_KEY (or LLM_API_KEY)",
            route=ROUTE_EMBED,
        )
        embedding = data["data"][0]["embedding"]
        # Embeddings are metered as their own call kind (not chat calls) so
        # the hop budget is unaffected but cost/usage stay complete (§5.3).
        if self.budget:
            usage = data.get("usage") or {}
            self.budget.record_embedding(
                int(usage.get("prompt_tokens", 0) or usage.get("total_tokens", 0))
            )
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
        rows = sorted(data["data"], key=lambda row: row["index"])
        if [row["index"] for row in rows] != list(range(len(texts))):
            raise ValueError("Embedding response indexes do not match the input batch")
        if self.budget:
            self.budget.record_embedding(int((data.get("usage") or {}).get("total_tokens", 0)))
        return [list(row["embedding"]) for row in rows]

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
        if not api_key:
            raise RuntimeError(
                f"{key_env} is not set. Configure .env or use MockLLMProvider in tests."
            )
        breaker = self.circuits.route(route)
        if not breaker.allow():
            raise RuntimeError(f"LLM circuit open ({route}): too many failures")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # Connect must fail fast: SSL hang otherwise dominates Fast Path P95.
        connect_s = min(3.0, float(self.timeout_seconds))
        timeout = httpx.Timeout(
            connect=connect_s,
            read=float(self.timeout_seconds),
            write=min(30.0, float(self.timeout_seconds)),
            pool=connect_s,
        )
        try:
            with httpx.Client(base_url=base_url, timeout=timeout) as client:
                resp = client.post(path, headers=headers, json=payload)
                resp.raise_for_status()
                data = JSON_OBJECT.validate_python(resp.json())
        except Exception:
            breaker.record_failure()
            raise
        breaker.record_success()
        return data


from agentic_graphrag.llm.mock import MockLLMProvider as MockLLMProvider  # noqa: E402
