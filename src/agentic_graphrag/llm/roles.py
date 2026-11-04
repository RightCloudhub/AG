"""Select semantic roles while keeping legacy completion clients compatible."""

from typing import Protocol, overload, runtime_checkable

from agentic_graphrag.core.models import ModelRole
from agentic_graphrag.core.ports.llm import CompletionClient, LLMClient


@runtime_checkable
class RoleClientFactory(Protocol):
    def for_role(self, role: ModelRole) -> LLMClient: ...


@overload
def for_role(llm: LLMClient, role: ModelRole) -> LLMClient: ...


@overload
def for_role(llm: CompletionClient, role: ModelRole) -> CompletionClient: ...


def for_role(llm: CompletionClient, role: ModelRole) -> CompletionClient:
    return llm.for_role(role) if isinstance(llm, RoleClientFactory) else llm
