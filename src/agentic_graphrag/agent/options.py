"""Shared option dataclasses for agent loop entrypoints (param-limit hygiene)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentic_graphrag.agent.executor import Executor
    from agentic_graphrag.agent.guardrails import GuardrailConfig
    from agentic_graphrag.llm.budget import BudgetTracker
    from agentic_graphrag.llm.provider import LLMProvider


@dataclass(frozen=True)
class AgentDeps:
    """Core runtime wiring: executor + LLM + guardrails (+ optional budget)."""

    executor: Executor
    llm: LLMProvider | None
    guard_cfg: GuardrailConfig
    budget: BudgetTracker | None = None


@dataclass(frozen=True)
class AgentRunOptions:
    """Keyword options for :func:`run_agentic_query`."""

    guard_cfg: GuardrailConfig | None = None
    budget: BudgetTracker | None = None
    allow_llm: bool = True
    recursion_limit: int | None = None
    checkpointer: Any | None = None
    thread_id: str | None = None


@dataclass(frozen=True)
class QueryOptions(AgentRunOptions):
    """Keyword options for :func:`run_query` (triage + agentic)."""

    force_agentic: bool = False
    enable_triage: bool = True
    known_entities: list[str] | None = None


@dataclass
class CritiqueContext:
    """Inputs for :func:`critique` (beyond the LLM provider)."""

    question: str
    sub_question: str
    evidence: list[Any]
    explored_paths: list[str] = field(default_factory=list)
    hop: int = 1
    max_hops: int = 5
    remaining_subquestions: int = 0
    excluded_hypotheses: list[str] = field(default_factory=list)
