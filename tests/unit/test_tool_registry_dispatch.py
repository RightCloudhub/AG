"""Refactor §1.2: executor dispatch goes through the tool registry."""

from __future__ import annotations

from agentic_graphrag.agent.executor_dispatch import TOOL_HANDLERS, dispatch
from agentic_graphrag.agent.tools import ToolSpec, default_retrieval_tools


def test_registry_names_match_dispatch_table() -> None:
    reg = default_retrieval_tools()
    assert {t.name for t in reg.list()} == set(TOOL_HANDLERS)
    assert all(reg.has(name) for name in TOOL_HANDLERS)


def test_dispatch_routes_through_registry_middleware() -> None:
    seen: list[str] = []

    def mw(spec, kwargs, call_next):
        seen.append(spec.name)
        return call_next(kwargs)

    reg = default_retrieval_tools([mw])
    reg.register(
        ToolSpec(name="echo", description="test", handler=lambda **kw: [kw["sub_question"]])
    )

    class _Exec:
        tools = reg

    assert dispatch(_Exec(), "echo", {}, sub_question="q") == ["q"]
    assert seen == ["echo"]
    assert dispatch(_Exec(), "missing_tool", {}, sub_question="q") == []


def test_middleware_order_outermost_first() -> None:
    order: list[str] = []

    def outer(spec, kw, nxt):
        order.append("outer")
        return nxt(kw)

    def inner(spec, kw, nxt):
        order.append("inner")
        return nxt(kw)

    reg = default_retrieval_tools([outer])
    reg.use(inner)
    reg.register(ToolSpec(name="t", description="", handler=lambda **kw: 1))
    assert reg.invoke("t") == 1
    assert order == ["outer", "inner"]
