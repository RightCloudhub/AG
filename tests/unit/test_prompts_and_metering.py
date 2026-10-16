"""Refactor §5.3 / §5.4: embedding metering and versioned prompts."""

from __future__ import annotations

from agentic_graphrag.config import load_prompt
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.prompts import prompt_version, prompt_versions, reset_prompt_versions


def test_embedding_is_metered_but_not_a_chat_call() -> None:
    b = BudgetTracker(max_llm_calls=1, max_tokens=1000)
    b.record_embedding(10)
    b.record_embedding(5)
    snap = b.snapshot()
    assert snap["embed_calls"] == 2 and snap["llm_calls"] == 0 and snap["prompt_tokens"] == 15
    b.check()  # chat budget untouched


def test_load_prompt_records_content_version() -> None:
    reset_prompt_versions()
    text = load_prompt("entailment")
    assert "fact-checker" in text
    assert prompt_versions() == {"entailment": prompt_version(text)}


def test_entailment_prompt_comes_from_registry() -> None:
    from agentic_graphrag.generation.entailment import _system_prompt

    assert _system_prompt().startswith("You are a strict fact-checker")
