"""Settle completed remote calls even when their response payload is unusable."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.llm.responses import CompletionResponse, CompletionUsage, completion_text


def settle_completion(
    data: dict[str, Any], budget: BudgetTracker | None
) -> tuple[str, dict[str, Any]]:
    usage = CompletionUsage()
    try:
        usage = CompletionUsage.model_validate(data.get("usage") or {})
        response = CompletionResponse.model_validate(data)
    except ValidationError:
        if budget:
            budget.note_parse_failure()
        raise
    finally:
        # A successful HTTP response has already spent a call. Invalid content
        # cannot return that slot; invalid usage leaves its token count unknown.
        if budget:
            budget.settle_call(usage.prompt_tokens, usage.completion_tokens)
    return completion_text(response), usage.model_dump()


def settle_embedding(data: dict[str, Any], budget: BudgetTracker | None) -> None:
    if budget is None:
        return
    tokens = 0
    try:
        usage = data.get("usage") or {}
        tokens = int(usage.get("prompt_tokens", 0) or usage.get("total_tokens", 0))
    finally:
        # Index/vector validation happens after accounting, just as with chat.
        budget.record_embedding(tokens)
