"""Role selection creates clients without mutating a shared provider."""

from __future__ import annotations

from copy import copy
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agentic_graphrag.config_roles import ModelRole, RoleModelConfig
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.llm.circuit import CircuitBreakerSet

if TYPE_CHECKING:
    from agentic_graphrag.llm.provider import LLMProvider


@runtime_checkable
class RoleFactory(Protocol):
    def for_role(self, role: ModelRole) -> LLMClient: ...


def role_client(llm: LLMClient, role: ModelRole) -> LLMClient:
    return llm.for_role(role) if isinstance(llm, RoleFactory) else llm


def select_role(provider: LLMProvider, role: ModelRole) -> LLMProvider:
    client = copy(provider)
    client.role_config = provider.roles.for_role(role)
    default = provider.light_model if role in {"triage", "verifier"} else provider.strong_model
    client.strong_model = client.light_model = client.role_config.model or default
    client.fallback_to_light = False
    client.circuits = CircuitBreakerSet(provider.circuits.template)
    return client


def model_name(llm: LLMClient) -> str:
    return str(getattr(llm, "strong_model", type(llm).__name__))


def usage_cost(llm: LLMClient, prompt: int, completion: int) -> float | None:
    config = getattr(llm, "role_config", None)
    return config.cost(prompt, completion) if isinstance(config, RoleModelConfig) else None
