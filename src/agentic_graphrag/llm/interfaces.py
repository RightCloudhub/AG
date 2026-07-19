"""LLM provider Protocol (NFR-10 / P2-ARCH-02).

Concrete implementations: ``LLMProvider`` (OpenAI-compatible HTTP) and
``MockLLMProvider`` (deterministic unit tests). Agent / retrieval code should
type against this protocol so providers stay swappable.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentic_graphrag.llm.provider import Message, Tier


@runtime_checkable
class LLMClient(Protocol):
    """Minimal surface used by agent, retrieval, and knowledge extraction."""

    def complete(
        self,
        messages: list[Message],
        *,
        tier: Tier = Tier.STRONG,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str: ...

    def embed(self, text: str) -> list[float]: ...

    def embed_many(self, texts: list[str]) -> list[list[float]]: ...
