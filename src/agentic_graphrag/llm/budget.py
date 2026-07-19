"""Per-query LLM cost / call budget (FR-AG-06, FR-OP-02)."""

from __future__ import annotations

from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    """Raised when a hard budget guardrail is hit."""

    def __init__(self, reason: str, tracker: BudgetTracker) -> None:
        super().__init__(reason)
        self.reason = reason
        self.tracker = tracker


@dataclass
class BudgetTracker:
    max_llm_calls: int = 20
    max_tokens: int = 50_000
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    events: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def check(self) -> None:
        if self.llm_calls >= self.max_llm_calls:
            raise BudgetExceeded(
                f"max_llm_calls reached ({self.llm_calls}/{self.max_llm_calls})",
                self,
            )
        if self.total_tokens >= self.max_tokens:
            raise BudgetExceeded(
                f"max_tokens reached ({self.total_tokens}/{self.max_tokens})",
                self,
            )

    def record_call(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        self.check()
        self.llm_calls += 1
        self.prompt_tokens += max(0, prompt_tokens)
        self.completion_tokens += max(0, completion_tokens)
        self.events.append(
            f"call#{self.llm_calls} prompt={prompt_tokens} completion={completion_tokens}"
        )

    def snapshot(self) -> dict[str, int]:
        return {
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }
