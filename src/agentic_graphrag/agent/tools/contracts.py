"""Typed signatures of built-in retrieval tool handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from agentic_graphrag.core.evidence import Candidate

if TYPE_CHECKING:
    from agentic_graphrag.agent.executor import Executor


class ToolHandler(Protocol):
    def __call__(
        self,
        executor: Executor,
        args: dict[str, Any],
        sub_question: str,
        *,
        tenant_id: str | None = None,
    ) -> list[Candidate]: ...
