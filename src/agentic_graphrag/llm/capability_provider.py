"""A pinned model exposes only its declared, tested capabilities."""

from __future__ import annotations

import math
from typing import Any

from agentic_graphrag.core.models import ModelCapabilities
from agentic_graphrag.core.ports.llm import Message, Tier
from agentic_graphrag.llm.provider import LLMProvider
from agentic_graphrag.llm.provider_completion import record_embedding_usage


class CapabilityProvider(LLMProvider):
    capabilities: ModelCapabilities

    def complete(
        self,
        messages: list[Message],
        *,
        tier: Tier = Tier.STRONG,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        if not self.capabilities.completion:
            raise ValueError("Selected model does not provide completions")
        return super().complete(
            messages, tier=tier, temperature=temperature, response_format=response_format
        )

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not self.capabilities.embedding:
            raise ValueError("Selected model does not provide embeddings")
        return super().embed_many(texts)

    def embed_for_model(self, text: str, model: str) -> list[float]:
        if not self.capabilities.embedding or model != self.embedding_model:
            raise ValueError("Selected model does not provide the required embedding space")
        return super().embed_for_model(text, model)

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        if not self.capabilities.rerank:
            raise ValueError("Selected model does not provide reranking")
        if not texts:
            return []
        payload: dict[str, Any] = {"model": self.strong_model, "query": query}
        if self.capabilities.rerank_protocol == "tei":
            payload.update(texts=texts, truncate=True)
        else:
            payload.update(documents=texts, top_n=len(texts))
        data = self._post(
            "/rerank", payload, base_url=self.base_url, api_key=self.api_key, route="rerank"
        )
        record_embedding_usage(self, data, kind="rerank")
        return rerank_scores(data.get("results"), count=len(texts))


def rerank_scores(rows: object, *, count: int) -> list[float]:
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError("Rerank response must include each input exactly once")
    scores: dict[int, float] = {}
    for row in rows:
        index = row.get("index")
        if type(index) is not int or index not in range(count) or index in scores:
            raise ValueError("Invalid or duplicate rerank index")
        value = row.get("relevance_score", row.get("score"))
        if type(value) not in {float, int} or not math.isfinite(value):
            raise ValueError("Rerank score must be finite")
        scores[index] = float(value)
    return [scores[index] for index in range(count)]
