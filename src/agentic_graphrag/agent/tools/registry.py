"""Tool registry: the single dispatch point for every agent tool (refactor §1.2).

Every retrieval capability is a :class:`ToolSpec` (schema, cost, timeout,
permissions, handler). The executor only talks to a :class:`ToolRegistry`;
cross-cutting concerns (guardrails, budget, tracing) attach once as
:data:`ToolMiddleware` around :meth:`ToolRegistry.invoke` instead of being
sprinkled through handlers. External / MCP tools register the same way.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# A middleware receives the spec, the kwargs, and ``call_next`` (the rest of the
# chain). It may short-circuit, transform kwargs, or observe the result.
ToolMiddleware = Callable[["ToolSpec", dict[str, Any], Callable[[dict[str, Any]], Any]], Any]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    permissions: list[str] = field(default_factory=list)
    handler: Callable[..., Any] | None = None
    enabled: bool = True
    cost_units: float = 0.0
    timeout_s: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters_schema": self.parameters_schema,
            "permissions": list(self.permissions),
            "enabled": self.enabled,
            "cost_units": self.cost_units,
            "timeout_s": self.timeout_s,
        }


# Backwards-compatible alias (pre-refactor name).
ExternalToolSpec = ToolSpec


class ToolRegistry:
    def __init__(self, middleware: list[ToolMiddleware] | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._middleware: list[ToolMiddleware] = list(middleware or [])

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def use(self, middleware: ToolMiddleware) -> None:
        """Append a middleware; the first registered is the outermost."""
        self._middleware.append(middleware)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        spec = self._tools.get(name)
        return spec is not None and spec.enabled and spec.handler is not None

    def list(self, *, enabled_only: bool = True) -> list[ToolSpec]:
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
        handler = spec.handler

        def terminal(kw: dict[str, Any]) -> Any:
            return handler(**kw)

        call: Callable[[dict[str, Any]], Any] = terminal
        for mw in reversed(self._middleware):
            call = _bind(mw, spec, call)
        return call(dict(kwargs))

    def describe_for_llm(self) -> str:
        lines = []
        for t in self.list():
            lines.append(f"- {t.name}: {t.description} (perms={t.permissions})")
        return "\n".join(lines) or "(no tools)"


def _bind(
    mw: ToolMiddleware, spec: ToolSpec, nxt: Callable[[dict[str, Any]], Any]
) -> Callable[[dict[str, Any]], Any]:
    return lambda kw: mw(spec, kw, nxt)


_RETRIEVAL_TOOLS: list[tuple[str, str, float]] = [
    ("graph_neighbors", "k-hop graph neighbors for an entity", 1.0),
    ("graph_path", "paths between two entities", 2.0),
    ("graph_subgraph", "subgraph around seed entities", 2.0),
    ("vector_search", "semantic chunk retrieval", 1.0),
    ("fulltext_search", "BM25 keyword retrieval", 0.5),
]


def default_retrieval_tools(middleware: list[ToolMiddleware] | None = None) -> ToolRegistry:
    """Built-in retrieval tools, bound to the executor's handlers.

    Handlers take keyword args ``executor``, ``args``, ``sub_question``, ``tenant_id``.
    """
    from agentic_graphrag.agent.executor_dispatch import TOOL_HANDLERS

    reg = ToolRegistry(middleware)
    for name, desc, cost in _RETRIEVAL_TOOLS:
        handler = TOOL_HANDLERS[name]
        reg.register(
            ToolSpec(
                name=name,
                description=desc,
                parameters_schema={"type": "object"},
                permissions=["retrieval:read"],
                handler=_adapt(handler),
                cost_units=cost,
            )
        )
    return reg


def _adapt(handler: Callable[..., Any]) -> Callable[..., Any]:
    def call(*, executor: Any, args: dict[str, Any], sub_question: str, tenant_id: Any = None):
        return handler(executor, args, sub_question, tenant_id=tenant_id)

    return call
