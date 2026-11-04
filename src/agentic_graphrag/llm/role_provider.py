"""Semantic role routing with shared request budgets and isolated endpoint state."""

from __future__ import annotations

from typing import Any

from agentic_graphrag.core.models import ModelRole
from agentic_graphrag.core.ports.llm import Message, Tier
from agentic_graphrag.llm.circuit import CircuitBreaker
from agentic_graphrag.llm.provider import LLMProvider


class RoleLLMProvider(LLMProvider):
    """Default calls go to the researcher; callsites explicitly select other roles."""

    def __init__(self, clients: dict[ModelRole, LLMProvider], *, embedding_model: str) -> None:
        researcher = clients[ModelRole.RESEARCHER]
        super().__init__(api_key="", budget=researcher.budget)
        self.clients = clients
        self.strong_model = researcher.strong_model
        self.light_model = clients[ModelRole.TRIAGE].strong_model
        self.embedding_model = embedding_model

    def for_role(self, role: ModelRole) -> LLMProvider:
        if role not in self.clients:
            raise ValueError(f"Model role {role.value} is disabled")
        return self.clients[role]

    @property
    def usage_by_role(self) -> dict[str, dict[str, int]]:
        return {role.value: dict(client.usage) for role, client in self.clients.items()}

    def for_verifier(self, *, model: str | None = None) -> LLMProvider:
        verifier = self.for_role(ModelRole.VERIFIER)
        if model and model not in {verifier.strong_model, verifier.light_model}:
            raise ValueError("Legacy verifier override conflicts with the published role profile")
        return verifier

    def complete(
        self,
        messages: list[Message],
        *,
        tier: Tier = Tier.STRONG,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        return self.for_role(ModelRole.RESEARCHER).complete(
            messages, tier=tier, temperature=temperature, response_format=response_format
        )

    def format_for_schema(self, schema: dict[str, Any]) -> dict[str, Any] | None:
        return self.for_role(ModelRole.RESEARCHER).format_for_schema(schema)

    def embed(self, text: str) -> list[float]:
        return self.for_role(ModelRole.EMBEDDING).embed(text)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return self.for_role(ModelRole.EMBEDDING).embed_many(texts)

    def embed_for_model(self, text: str, model: str) -> list[float]:
        client = self.for_role(ModelRole.EMBEDDING)
        if model != self.embedding_model:
            raise ValueError("Embedding model differs from the published index space")
        return client.embed(text)

    @property
    def circuit(self) -> CircuitBreaker:
        return self.for_role(ModelRole.RESEARCHER).circuit

    def close(self) -> None:
        for client in self.clients.values():
            client.close()
        super().close()
