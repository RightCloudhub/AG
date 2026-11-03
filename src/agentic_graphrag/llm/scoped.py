"""Budget-scoped clients without mutating a shared LLM provider."""

from typing import Any, Protocol, runtime_checkable

from agentic_graphrag.core.ports.llm import LLMClient, Message
from agentic_graphrag.governance.budget import BudgetExceeded, BudgetTracker

MESSAGE_TOKEN_ALLOWANCE = 128


def check_embedding_budget(text: str, budget: BudgetTracker | None) -> None:
    if (
        budget
        and len(text.encode("utf-8")) + MESSAGE_TOKEN_ALLOWANCE
        > budget.max_tokens - budget.total_tokens
    ):
        raise BudgetExceeded("embedding exceeds scoped token allowance", budget)


@runtime_checkable
class ScopedFactory(Protocol):
    def for_budget(self, budget: BudgetTracker, *, max_output_tokens: int) -> LLMClient: ...


def scoped_client(llm: LLMClient, budget: BudgetTracker, *, max_output_tokens: int) -> LLMClient:
    if not isinstance(llm, ScopedFactory):
        raise ValueError("multi mode requires an LLM client with for_budget support")
    return llm.for_budget(budget, max_output_tokens=max_output_tokens)


def completion_payload(
    messages: list[Message],
    model: str,
    *,
    temperature: float,
    response_format: dict[str, Any] | None,
    max_output_tokens: int | None,
    budget: BudgetTracker | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "temperature": temperature,
    }
    if response_format:
        payload["response_format"] = response_format
    if max_output_tokens is not None:
        prompt_bound = sum(
            len(m.content.encode("utf-8")) + MESSAGE_TOKEN_ALLOWANCE for m in messages
        )
        if budget and prompt_bound + max_output_tokens > budget.max_tokens - budget.total_tokens:
            raise BudgetExceeded("scoped prompt and completion exceed token allowance", budget)
        payload["max_tokens"] = max_output_tokens
    return payload
