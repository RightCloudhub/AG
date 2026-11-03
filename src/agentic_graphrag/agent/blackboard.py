"""Append-only worker output and budget contracts for the shared blackboard.

Workers propose changes; only the merge node publishes a register through
``apply_invariants``. Stable entry ids make checkpoint replay idempotent.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.agent.requirements import Requirement
from agentic_graphrag.core.reasoning import ToolCallTrace

type Role = Literal["triage", "researcher", "verifier", "writer"]
type BudgetTier = Literal["small", "standard", "summary"]


class Fact(BaseModel):
    """A scoped assertion; distinct values trigger explicit conflict research."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    entity: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    value: str = Field(min_length=1)
    scope: str = ""
    passages: set[str] = Field(default_factory=set)


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_id: str
    branch_id: str
    tenant_id: str
    run_id: str
    round: int = 0
    requirement: Requirement
    facts: list[Fact] = Field(default_factory=list)
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)


class BranchEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_id: str
    branch_id: str
    tenant_id: str
    run_id: str
    req_id: str
    role: Role
    model: str = ""
    round: int = 0
    tokens: int = 0
    outcome: str = ""
    detail: str = ""


class BudgetEntry(BaseModel):
    """One allocation or usage receipt, attributable to run, requirement and role."""

    model_config = ConfigDict(extra="forbid")
    entry_id: str
    branch_id: str
    tenant_id: str
    run_id: str
    req_id: str
    role: Role
    kind: Literal["allocation", "usage"] = "usage"
    model: str = ""
    calls: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    token_limit: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def append_records[T: Proposal | BranchEvent | BudgetEntry](
    previous: list[T], incoming: list[T]
) -> list[T]:
    """Append once without mutating inputs; mismatched replay is an error."""
    records = {item.entry_id: item for item in previous}
    for item in incoming:
        if item.entry_id in records and records[item.entry_id] != item:
            raise ValueError(f"conflicting blackboard entry: {item.entry_id}")
        records.setdefault(item.entry_id, item)
    return list(records.values())


def usage_totals(entries: list[BudgetEntry]) -> dict[str, int]:
    usage = [entry for entry in entries if entry.kind == "usage"]
    return {
        "llm_calls": sum(entry.calls for entry in usage),
        "prompt_tokens": sum(entry.prompt_tokens for entry in usage),
        "completion_tokens": sum(entry.completion_tokens for entry in usage),
        "total_tokens": sum(entry.tokens for entry in usage),
        "tool_calls": sum(entry.tool_calls for entry in usage),
    }
