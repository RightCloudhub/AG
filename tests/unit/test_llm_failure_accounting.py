"""Budget accounting survives rejected payloads, exhausted tokens and cancellation."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from agentic_graphrag.core.enums import LLMRole
from agentic_graphrag.core.ports.llm import Message
from agentic_graphrag.governance.budget import BudgetExceeded, BudgetTracker, role_scope
from agentic_graphrag.llm.provider import LLMProvider


def _messages() -> list[Message]:
    return [Message(role="user", content="question")]


@pytest.mark.parametrize("method", ["embed", "embed_for_model", "embed_many"])
def test_exhausted_token_budget_prevents_remote_embedding(monkeypatch, method: str) -> None:
    budget = BudgetTracker(max_tokens=3, prompt_tokens=3)
    llm = LLMProvider(api_key="stub", budget=budget)
    remote_calls: list[str] = []

    def post(path: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        remote_calls.append(path)
        return {"data": [{"embedding": [0.1], "index": 0}], "usage": {"total_tokens": 1}}

    monkeypatch.setattr(llm, "_post", post)
    args = {"embed": ("q",), "embed_for_model": ("q", "model"), "embed_many": (["q"],)}

    with pytest.raises(BudgetExceeded, match="max_tokens"):
        getattr(llm, method)(*args[method])

    assert remote_calls == []
    assert budget.embed_calls == 0


def test_embedding_does_not_consume_or_require_a_chat_slot(monkeypatch) -> None:
    budget = BudgetTracker(max_llm_calls=1, llm_calls=1)
    llm = LLMProvider(api_key="stub", budget=budget)
    monkeypatch.setattr(
        llm,
        "_post",
        lambda *_args, **_kwargs: {"data": [{"embedding": [0.1]}], "usage": {"total_tokens": 3}},
    )

    assert llm.embed("q") == [0.1]
    assert budget.llm_calls == 1 and budget.embed_calls == 1
    assert budget.total_tokens == 3


def test_malformed_completion_still_charges_reported_usage(monkeypatch) -> None:
    budget = BudgetTracker(max_llm_calls=1)
    llm = LLMProvider(api_key="stub", budget=budget)
    monkeypatch.setattr(
        llm,
        "_post",
        lambda *_args, **_kwargs: {
            "choices": [],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        },
    )

    with role_scope(LLMRole.PLANNER), pytest.raises(ValidationError):
        llm.complete(_messages())

    assert budget.llm_calls == 1 and budget.total_tokens == 10
    assert budget.role_usage()["planner"]["calls"] == 1
    assert budget.role_usage()["planner"]["parse_failures"] == 1
    with pytest.raises(BudgetExceeded):
        llm.complete(_messages())


def test_invalid_usage_does_not_release_a_successful_remote_call(monkeypatch) -> None:
    budget = BudgetTracker(max_llm_calls=1)
    llm = LLMProvider(api_key="stub", budget=budget)
    monkeypatch.setattr(
        llm,
        "_post",
        lambda *_args, **_kwargs: {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": "invalid"},
        },
    )

    with pytest.raises(ValidationError):
        llm.complete(_messages())

    assert budget.llm_calls == 1
    assert budget.role_usage()["unknown"]["calls"] == 1


def test_invalid_embedding_batch_still_charges_reported_usage(monkeypatch) -> None:
    budget = BudgetTracker()
    llm = LLMProvider(api_key="stub", budget=budget)
    monkeypatch.setattr(
        llm,
        "_post",
        lambda *_args, **_kwargs: {
            "data": [{"embedding": [0.1], "index": 3}],
            "usage": {"total_tokens": 3},
        },
    )

    with pytest.raises(ValueError, match="indexes"):
        llm.embed_many(["q"])

    assert budget.embed_calls == 1 and budget.total_tokens == 3


def test_cancelled_remote_call_releases_its_reserved_slot(monkeypatch) -> None:
    budget = BudgetTracker(max_llm_calls=1)
    llm = LLMProvider(api_key="stub", budget=budget)

    def cancel(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise asyncio.CancelledError

    monkeypatch.setattr(llm, "_post", cancel)
    with pytest.raises(asyncio.CancelledError):
        llm.complete(_messages())

    assert budget.llm_calls == 0
    budget.check()
