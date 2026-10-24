"""LLM provider Protocol (NFR-10 / P2-ARCH-02).

Concrete implementations: ``LLMProvider`` (OpenAI-compatible HTTP) and
``MockLLMProvider`` (deterministic unit tests). Agent / retrieval code should
type against this protocol so providers stay swappable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from agentic_graphrag.core.enums import MessageRole


class Tier(StrEnum):
    STRONG = "strong"
    LIGHT = "light"


@dataclass
class Message:
    role: MessageRole
    content: str

    def __post_init__(self) -> None:
        self.role = MessageRole(self.role)


@runtime_checkable
class CompletionClient(Protocol):
    """Text generation capability independent of embedding support."""

    def complete(
        self,
        messages: list[Message],
        *,
        tier: Tier = Tier.STRONG,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str: ...


@runtime_checkable
class LLMClient(CompletionClient, Protocol):
    """Generation and embedding surface used by combined runtime providers."""

    def embed(self, text: str) -> list[float]: ...

    def embed_many(self, texts: list[str]) -> list[list[float]]: ...


class BudgetReporter(Protocol):
    def snapshot(self) -> Mapping[str, int]: ...


@runtime_checkable
class MeteredLLMClient(LLMClient, Protocol):
    @property
    def budget(self) -> BudgetReporter | None: ...


@runtime_checkable
class EntailmentPolicy(Protocol):
    @property
    def supports_entailment(self) -> bool: ...


class CircuitGate(Protocol):
    def allow(self) -> bool: ...


@runtime_checkable
class CircuitControlledClient(LLMClient, Protocol):
    @property
    def circuit(self) -> CircuitGate: ...
