"""Malformed upstream responses cannot escape as incorrectly typed results."""

import pytest

from agentic_graphrag.core.enums import MessageRole
from agentic_graphrag.core.ports.llm import Message
from agentic_graphrag.llm.provider import LLMProvider


def test_non_text_completion_is_rejected(monkeypatch) -> None:
    provider = LLMProvider(api_key="test", base_url="http://offline.test")
    monkeypatch.setattr(
        provider,
        "_post",
        lambda *args, **kwargs: {"choices": [{"message": {"content": 123}}]},
    )
    with pytest.raises(ValueError):
        provider.complete([Message(role=MessageRole.USER, content="question")])
