"""Deterministic LLM stub; imported through provider for compatibility."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agentic_graphrag.core.ports.llm import Message, Tier
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.llm.provider import LLMProvider


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
