"""Structured JSON output with parse retry (LLM interaction robustness)."""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from agentic_graphrag.llm.provider import LLMProvider, Message, Tier

T = TypeVar("T", bound=BaseModel)

_JSON_BLOCK = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_json(text: str) -> Any:
    text = text.strip()
    match = _JSON_BLOCK.search(text)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


def complete_structured(
    llm: LLMProvider,
    messages: list[Message],
    model_type: type[T],
    *,
    tier: Tier = Tier.STRONG,
    max_retries: int = 2,
) -> T:
    """Call LLM and parse into a Pydantic model with limited retries."""
    history = list(messages)
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        raw = llm.complete(
            history,
            tier=tier,
            response_format={"type": "json_object"},
        )
        try:
            data = extract_json(raw)
            return model_type.model_validate(data)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = exc
            history = list(history) + [
                Message(role="assistant", content=raw),
                Message(
                    role="user",
                    content=(
                        f"Your previous response was invalid JSON for the required schema. "
                        f"Error: {exc}. Reply with corrected JSON only."
                    ),
                ),
            ]
    raise ValueError(f"Failed to parse structured output after retries: {last_error}")
