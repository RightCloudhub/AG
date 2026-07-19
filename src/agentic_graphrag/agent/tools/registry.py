"""Tool registration with name/description/schema/permission (FR-AG-08)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExternalToolSpec:
    name: str
    description: str
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    permissions: list[str] = field(default_factory=list)
    handler: Callable[..., Any] | None = None
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters_schema": self.parameters_schema,
            "permissions": list(self.permissions),
            "enabled": self.enabled,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ExternalToolSpec] = {}

    def register(self, spec: ExternalToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ExternalToolSpec | None:
        return self._tools.get(name)

    def list(self, *, enabled_only: bool = True) -> list[ExternalToolSpec]:
        tools = list(self._tools.values())
        if enabled_only:
            tools = [t for t in tools if t.enabled]
        return tools

    def invoke(self, name: str, **kwargs: Any) -> Any:
        spec = self._tools.get(name)
        if spec is None or not spec.enabled:
            raise KeyError(f"tool not found or disabled: {name}")
        if spec.handler is None:
            raise NotImplementedError(f"tool {name} has no handler")
        return spec.handler(**kwargs)

    def describe_for_llm(self) -> str:
        lines = []
        for t in self.list():
            lines.append(f"- {t.name}: {t.description} (perms={t.permissions})")
        return "\n".join(lines) or "(no tools)"


def default_retrieval_tools() -> ToolRegistry:
    """Built-in retrieval tool descriptors (handlers wired in Executor)."""
    reg = ToolRegistry()
    for name, desc in [
        ("graph_neighbors", "k-hop graph neighbors for an entity"),
        ("graph_paths", "paths between two entities"),
        ("graph_subgraph", "subgraph around seed entities"),
        ("vector_search", "semantic chunk retrieval"),
        ("fulltext_search", "BM25 keyword retrieval"),
    ]:
        reg.register(
            ExternalToolSpec(
                name=name,
                description=desc,
                parameters_schema={"type": "object"},
                permissions=["retrieval:read"],
            )
        )
    return reg
