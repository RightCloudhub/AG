"""Shared emit context for gold template generation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from agentic_graphrag.eval.cases import EvalCase
from agentic_graphrag.eval.gold_index import Edge


@dataclass
class EmitContext:
    """Mutable state for a single emit_* pass over the edge index."""

    edges: list[Edge]
    out_adj: dict[str, list[Edge]]
    in_adj: dict[str, list[Edge]]
    add: Callable[[EvalCase], bool | None]
    max_n: int
    count: int = 0
    # Optional flags for templates that gated on adj presence historically.
    has_out_adj: bool = True
    has_in_adj: bool = True
    # Extra scratch used by a few templates (kept out of positional args).
    extras: dict[str, object] = field(default_factory=dict)

    def full(self) -> bool:
        return self.count >= self.max_n

    def try_add(self, case: EvalCase) -> bool:
        """Add case; bump count only when the callback keeps it."""
        if self.add(case):
            self.count += 1
            return True
        return False

    def case_id(self, prefix: str) -> str:
        return f"{prefix}-{self.count:04d}"
