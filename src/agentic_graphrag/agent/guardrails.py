"""Loop guardrails: hops, LLM calls, tokens (FR-AG-06/07)."""

from __future__ import annotations

from dataclasses import dataclass

from agentic_graphrag.llm.budget import BudgetExceeded, BudgetTracker


@dataclass
class GuardrailConfig:
    max_hops: int = 5
    max_llm_calls: int = 20
    max_tokens: int = 50_000


@dataclass
class GuardrailState:
    hop: int = 0
    tripped: bool = False
    reason: str = ""


class Guardrails:
    def __init__(self, config: GuardrailConfig, budget: BudgetTracker | None = None) -> None:
        self.config = config
        self.budget = budget or BudgetTracker(
            max_llm_calls=config.max_llm_calls,
            max_tokens=config.max_tokens,
        )
        self.state = GuardrailState()

    def on_hop_start(self) -> None:
        self.state.hop += 1
        self._check()

    def _check(self) -> None:
        if self.state.hop > self.config.max_hops:
            self.state.tripped = True
            self.state.reason = f"max_hops exceeded ({self.state.hop}/{self.config.max_hops})"
            return
        try:
            self.budget.check()
        except BudgetExceeded as exc:
            self.state.tripped = True
            self.state.reason = str(exc.reason)

    def assert_ok(self) -> None:
        self._check()
        if self.state.tripped:
            raise BudgetExceeded(self.state.reason or "guardrail tripped", self.budget)

    def status_text(self) -> str:
        if self.state.tripped:
            return f"tripped: {self.state.reason}"
        return (
            f"ok hop={self.state.hop}/{self.config.max_hops} "
            f"llm_calls={self.budget.llm_calls}/{self.config.max_llm_calls} "
            f"tokens={self.budget.total_tokens}/{self.config.max_tokens}"
        )
