"""Refactor §1.1: one pipeline; non-streaming == drained streaming."""

from __future__ import annotations

from agentic_graphrag.agent import pipeline
from agentic_graphrag.agent.pipeline import EVENT_FINAL_CHAIN, EVENT_TRIAGE, drain
from agentic_graphrag.core.reasoning import ReasoningChain


def test_drain_returns_final_chain() -> None:
    chain = ReasoningChain(question="q", route="fast_path")
    events = iter([(EVENT_TRIAGE, {}), ("hop_done", {}), (EVENT_FINAL_CHAIN, chain)])
    assert drain(events) is chain


def test_drain_fails_closed_without_final_chain() -> None:
    import pytest

    with pytest.raises(pipeline.AgentStreamEmptyError):
        drain(iter([(EVENT_TRIAGE, {})]))


def test_run_query_is_the_drained_pipeline(monkeypatch) -> None:
    from agentic_graphrag.agent import loop

    sentinel = ReasoningChain(question="q", route="agentic")
    calls: list[str] = []

    def fake_iter(question, executor, llm, *, options=None):
        calls.append(question)
        yield EVENT_TRIAGE, {}
        yield EVENT_FINAL_CHAIN, sentinel

    monkeypatch.setattr(pipeline, "iter_query_progress", fake_iter)
    assert loop.run_query("q", executor=None, llm=None) is sentinel  # type: ignore[arg-type]
    assert calls == ["q"]


def test_initial_state_seeds_inherited_evidence() -> None:
    from agentic_graphrag.core.evidence import Candidate

    chain = ReasoningChain(question="q", route="agentic")
    ev = [Candidate(id="c1", source="vector", content="x")]
    state = pipeline._initial_state("q", chain, allow_llm=False, tenant_id="t", seed_evidence=ev)
    assert state["tenant_id"] == "t"
    assert [e["id"] for e in state["evidence"]] == ["c1"]
