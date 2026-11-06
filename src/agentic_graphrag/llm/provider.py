"""OpenAI-compatible LLM provider with dual tiers and embedding support."""

from __future__ import annotations

import time
from copy import copy
from pathlib import Path
from typing import Any

import httpx as httpx  # Compatibility for provider-boundary HTTP stubs.

from agentic_graphrag.config_roles import ModelRole as LegacyModelRole
from agentic_graphrag.config_roles import RoleModelConfig, RolesConfig
from agentic_graphrag.core.constants import MILLISECONDS_PER_SECOND
from agentic_graphrag.core.enums import LLMRole, RetrievalChannel
from agentic_graphrag.core.models import ModelRole
from agentic_graphrag.core.ports.llm import Message, Tier
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.llm import response_cache
from agentic_graphrag.llm.accounting import settle_completion
from agentic_graphrag.llm.circuit import (
    ROUTE_EMBED,
    ROUTE_LIGHT,
    ROUTE_STRONG,
    CircuitBreaker,
    CircuitBreakerSet,
    note_call_failure,
)
from agentic_graphrag.llm.embedding_response import embedding_rows
from agentic_graphrag.llm.http_transport import ProviderHttpPool
from agentic_graphrag.llm.provider_completion import (
    complete,
    empty_usage,
    record_chat_usage,
    record_embedding_usage,
)
from agentic_graphrag.llm.provider_options import ProviderOptions
from agentic_graphrag.llm.roles import select_role
from agentic_graphrag.llm.scoped import check_embedding_budget
from agentic_graphrag.llm.transport import post_json
from agentic_graphrag.llm.verifier import warn_same_model
from agentic_graphrag.observability.degradation import REASON_TIER_FALLBACK, record_degradation

_CHAT_KEY_ENV = "LLM_API_KEY"
_EMBED_KEY_ENV = "LLM_EMBEDDING_API_KEY (or LLM_API_KEY)"


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
        circuits: CircuitBreakerSet | None = None,
        fallback_to_light: bool = False,
        roles: RolesConfig | None = None,
        options: ProviderOptions | None = None,
    ) -> None:
        self.options = options or ProviderOptions()
        self.http_pool = ProviderHttpPool()
        self.usage = empty_usage()
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
        self.cache_dir = response_cache.directory(cache_dir)
        # One breaker per route (O-8), cloned from ``circuit``; process-wide when passed in.
        self.circuits = circuits if circuits is not None else CircuitBreakerSet(circuit)
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

    def for_role(self, role: LegacyModelRole | ModelRole) -> LLMProvider:
        # Semantic callsite routing leaves a legacy single-provider client unchanged.
        return self if isinstance(role, ModelRole) else select_role(self, role)

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
        verifier.circuits = self.circuits.for_client(f"verifier:{selected}")
        return verifier

    def circuit_for(self, tier: Tier) -> CircuitBreaker:
        """Breaker a call at ``tier`` would go through now (LIGHT when degrading)."""
        strong = self.circuits.route(ROUTE_STRONG)
        light = self.circuits.route(ROUTE_LIGHT)
        if tier != Tier.STRONG:
            return light
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
        if self.budget:
            self.budget.note_tier_fallback()
        record_degradation(RetrievalChannel.LLM, REASON_TIER_FALLBACK)
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

    def _metered_chat(self, payload: dict[str, Any], tier: Tier) -> tuple[str, dict[str, Any]]:
        """Hold a budget slot across the HTTP call, so the cap binds before paying."""
        if self.budget:
            self.budget.reserve_call()
        try:
            data = self._post(
                "/chat/completions",
                payload,
                base_url=self.base_url,
                api_key=self.api_key,
                route=ROUTE_STRONG if tier == Tier.STRONG else ROUTE_LIGHT,
            )
        except BaseException:
            if self.budget:
                self.budget.release_call()
            raise
        try:
            return settle_completion(data, self.budget)
        finally:
            record_chat_usage(self, data)

    def embed(self, text: str) -> list[float]:
        return self.embed_for_model(text, self.embedding_model)

    def embed_for_model(self, text: str, model: str) -> list[float]:
        if self.max_output_tokens is not None:
            check_embedding_budget(text, self.budget)
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

        data = self._post_embeddings(payload)
        embedding = embedding_rows(data, count=1, dimensions=self.options.embedding_dimensions)[0]
        response_cache.write_cache(self.cache_dir, cache_key, {"embedding": embedding})
        return list(embedding)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        data = self._post_embeddings({"model": self.embedding_model, "input": texts})
        return embedding_rows(data, count=len(texts), dimensions=self.options.embedding_dimensions)

    def _post_embeddings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.budget:
            self.budget.check_tokens()
        data = self._post(
            "/embeddings",
            payload,
            base_url=self.embedding_base_url,
            api_key=self.embedding_api_key,
            route=ROUTE_EMBED,
        )
        record_embedding_usage(self, data, kind="embedding")
        return data

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        base_url: str,
        api_key: str,
        route: str = ROUTE_STRONG,
    ) -> dict[str, Any]:
        from agentic_graphrag.governance.execution import check_execution

        check_execution()
        if self.budget:
            self.budget.check_tokens()
        required = (
            self.options.embedding_auth_required
            if route == ROUTE_EMBED
            else self.options.auth_required
        )
        if required and not api_key:
            key_env = _EMBED_KEY_ENV if route == ROUTE_EMBED else _CHAT_KEY_ENV
            raise RuntimeError(
                f"{key_env} is not set. Configure .env or use MockLLMProvider in tests."
            )
        breaker = self.circuits.route(route)
        permit = breaker.acquire()
        if permit is None:
            raise RuntimeError(f"LLM circuit open ({route}): too many failures")
        started = time.perf_counter()
        try:
            data = post_json(
                path,
                payload,
                base_url=base_url,
                api_key=api_key,
                timeout_seconds=self.timeout_seconds,
                pool=self.http_pool,
            )
        except BaseException as exc:
            if isinstance(exc, Exception):
                note_call_failure(breaker, exc, permit=permit)
            self._meter_remote(route, started, failed=True)
            raise
        else:
            breaker.record_success(permit=permit)
            self._meter_remote(route, started, failed=False)
            return data
        finally:
            breaker.release_probe(permit=permit)

    def close(self) -> None:
        self.http_pool.close()

    def _meter_remote(self, route: str, started: float, *, failed: bool) -> None:
        if self.budget:
            elapsed_ms = int((time.perf_counter() - started) * MILLISECONDS_PER_SECOND)
            role = LLMRole.EMBEDDING if route == ROUTE_EMBED else None
            self.budget.record_remote(elapsed_ms, failed=failed, role=role)


from agentic_graphrag.llm.mock import MockLLMProvider as MockLLMProvider  # noqa: E402
