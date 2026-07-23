"""OpenAI-compatible LLM provider with dual tiers and embedding support."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from agentic_graphrag.llm.budget import BudgetTracker
from agentic_graphrag.llm.circuit import CircuitBreaker


class Tier(StrEnum):
    STRONG = "strong"
    LIGHT = "light"


@dataclass
class Message:
    role: str
    content: str


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
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.circuit = circuit or CircuitBreaker()
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def model_for(self, tier: Tier) -> str:
        return self.strong_model if tier == Tier.STRONG else self.light_model

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

        payload: dict[str, Any] = {
            "model": self.model_for(tier),
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature if temperature is None else temperature,
        }
        if response_format:
            payload["response_format"] = response_format

        cache_key = self._cache_key("chat", payload)
        cached = self._read_cache(cache_key)
        if cached is not None:
            if self.budget:
                usage = cached.get("usage") or {}
                self.budget.record_call(
                    int(usage.get("prompt_tokens", 0)),
                    int(usage.get("completion_tokens", 0)),
                )
            return str(cached["content"])

        data = self._post(
            "/chat/completions",
            payload,
            base_url=self.base_url,
            api_key=self.api_key,
            key_env="LLM_API_KEY",
        )
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        if self.budget:
            self.budget.record_call(
                int(usage.get("prompt_tokens", 0)),
                int(usage.get("completion_tokens", 0)),
            )
        self._write_cache(
            cache_key,
            {"content": content, "usage": usage},
        )
        return content

    def embed(self, text: str) -> list[float]:
        payload = {"model": self.embedding_model, "input": text}
        # Include endpoint in cache key so multi-provider caches do not collide.
        cache_key = self._cache_key(
            "embed",
            {"base_url": self.embedding_base_url, "payload": payload},
        )
        cached = self._read_cache(cache_key)
        if cached is not None:
            return list(cached["embedding"])

        data = self._post(
            "/embeddings",
            payload,
            base_url=self.embedding_base_url,
            api_key=self.embedding_api_key,
            key_env="LLM_EMBEDDING_API_KEY (or LLM_API_KEY)",
        )
        embedding = data["data"][0]["embedding"]
        # Embeddings do not count toward hop LLM call budget by default,
        # but we still record token usage if budget exists.
        if self.budget:
            usage = data.get("usage") or {}
            self.budget.prompt_tokens += int(
                usage.get("prompt_tokens", 0) or usage.get("total_tokens", 0)
            )
        self._write_cache(cache_key, {"embedding": embedding})
        return list(embedding)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        base_url: str,
        api_key: str,
        key_env: str = "LLM_API_KEY",
    ) -> dict[str, Any]:
        if not api_key:
            raise RuntimeError(
                f"{key_env} is not set. Configure .env or use MockLLMProvider in tests."
            )
        if not self.circuit.allow():
            raise RuntimeError("LLM circuit open: too many consecutive failures")
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
                data = resp.json()
        except Exception:
            self.circuit.record_failure()
            raise
        self.circuit.record_success()
        return data

    def _cache_key(self, kind: str, payload: dict[str, Any]) -> str:
        raw = json.dumps({"kind": kind, "payload": payload}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _read_cache(self, key: str) -> dict[str, Any] | None:
        if not self.cache_dir:
            return None
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_cache(self, key: str, value: dict[str, Any]) -> None:
        if not self.cache_dir:
            return
        path = self.cache_dir / f"{key}.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class MockLLMProvider(LLMProvider):
    """Deterministic stub for unit tests — no network."""

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
