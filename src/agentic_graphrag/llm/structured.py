"""Structured JSON output with parse retry (LLM interaction robustness)."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from agentic_graphrag.core.enums import LLMRole, MessageRole
from agentic_graphrag.core.ports.llm import CompletionClient, Message, Tier
from agentic_graphrag.governance.budget import BudgetTracker, role_scope
from agentic_graphrag.llm.output_format import StructuredFormatClient

_JSON_BLOCK = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_json(text: str) -> Any:
    text = text.strip()
    match = _JSON_BLOCK.search(text)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


def complete_structured[T: BaseModel](
    llm: CompletionClient,
    messages: list[Message],
    model_type: type[T],
    *,
    tier: Tier = Tier.STRONG,
    max_retries: int = 2,
    temperature: float | None = None,
    role: LLMRole = LLMRole.UNKNOWN,
) -> T:
    """Call LLM and parse into a Pydantic model with limited retries."""
    options: dict[str, Any] = {"tier": tier, "response_format": {"type": "json_object"}}
    if temperature is not None:
        options["temperature"] = temperature
    with role_scope(role):
        return _parse_with_retries(
            llm, messages, model_type, options=options, max_retries=max_retries
        )


def _parse_with_retries[T: BaseModel](
    llm: CompletionClient,
    messages: list[Message],
    model_type: type[T],
    *,
    options: dict[str, Any],
    max_retries: int,
) -> T:
    budget = getattr(llm, "budget", None)
    history = list(messages)
    response_format = (
        llm.format_for_schema(model_type.model_json_schema())
        if isinstance(llm, StructuredFormatClient)
        else {"type": "json_object"}
    )
    options["response_format"] = response_format
    last_error: Exception | None = None
    for _attempt in range(max_retries + 1):
        raw = llm.complete(history, **options)
        try:
            data = extract_json(raw)
            return model_type.model_validate(data)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = exc
            if isinstance(budget, BudgetTracker):
                budget.note_parse_failure()
            history = list(history) + [
                Message(role=MessageRole.ASSISTANT, content=raw),
                Message(
                    role=MessageRole.USER,
                    content=(
                        f"Your previous response was invalid JSON for the required schema. "
                        f"Error: {exc}. Reply with corrected JSON only."
                    ),
                ),
            ]
    raise ValueError(f"Failed to parse structured output after retries: {last_error}")
