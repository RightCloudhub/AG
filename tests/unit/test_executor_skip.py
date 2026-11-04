"""A skipped plan node spends no hop and never reaches the critic (ORCH-03)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from agentic_graphrag.agent import loop_runtime
from agentic_graphrag.agent.critic import CriticResult
from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import build_graph, invoke_config, run_agentic_query
from agentic_graphrag.agent.loop_stream_events import events_for_node
from agentic_graphrag.agent.options import AgentRunOptions, CritiqueContext
from agentic_graphrag.agent.pipeline import iter_agentic
from agentic_graphrag.agent.plan_dag import SubQuestion
from agentic_graphrag.agent.state import initial_state
from agentic_graphrag.core.enums import AgentNode, CriticAction, StreamEvent
from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.reasoning import ReasoningChain, ReasoningStep
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

QUESTION = "Who leads the supplier of Apex Holdings?"
FIRST = "Who supplies Apex Holdings?"
LAST = "Who founded Orion Systems?"
UNRESOLVED = "Who is the CEO of {from:sq9}?"


def _executor(monkeypatch) -> Executor:
    """Every retrieval returns a distinct hit so a step's evidence is identifiable."""
    executor = Executor(GraphRetriever(InMemoryGraphStore()))

    def run(sub_question: str, *_args, **_kwargs):
        hit = Candidate(
            id=f"hit:{sub_question}",
            source=CandidateSource.FULLTEXT_CHUNK,
            content=f"Evidence about {sub_question}",
            score=1.0,
        )
        return [hit], []

    monkeypatch.setattr(executor, "run", run)
    return executor


def _plan(monkeypatch, *texts: str) -> None:
    plan = [SubQuestion(id=f"sq{i}", text=text) for i, text in enumerate(texts, 1)]
    monkeypatch.setattr(loop_runtime, "plan", lambda *_args, **_kwargs: list(plan))


Verdict = Callable[[CritiqueContext], CriticResult]


def _sufficient(ctx: CritiqueContext) -> CriticResult:
    return CriticResult(action=CriticAction.SUFFICIENT, sub_answered=True, global_answered=False)


def _script_critic(monkeypatch, verdict: Verdict = _sufficient) -> list[str]:
    judged: list[str] = []

    def critique(ctx: CritiqueContext, *_args, **_kwargs) -> CriticResult:
        judged.append(ctx.sub_question)
        return verdict(ctx)

    monkeypatch.setattr(loop_runtime, "critique", critique)
    return judged


def _run(executor: Executor, **guard_overrides) -> ReasoningChain:
    guards = {"max_hops": 4, "max_sub_questions": 4, "parallel_branches": False, **guard_overrides}
    return run_agentic_query(
        QUESTION, executor, None, allow_llm=False, guard_cfg=GuardrailConfig(**guards)
    )


@pytest.mark.parametrize("skipped", [FIRST, UNRESOLVED])
def test_node_after_a_skip_still_runs_and_critic_only_judges_executed_nodes(
    monkeypatch, skipped
) -> None:
    _plan(monkeypatch, FIRST, skipped, LAST)
    judged = _script_critic(monkeypatch)

    chain = _run(_executor(monkeypatch))

    assert [s.sub_question for s in chain.steps] == [FIRST, LAST]
    assert judged == [FIRST, LAST]
    assert chain.steps[1].evidence_ids == [f"hit:{LAST}"]


def test_skip_does_not_overwrite_the_previous_verdict(monkeypatch) -> None:
    """The previous step keeps its own verdict, not one about a node that never ran."""

    def verdict(ctx: CritiqueContext) -> CriticResult:
        if ctx.sub_question == FIRST:
            return CriticResult(
                action=CriticAction.SUFFICIENT, sub_answered=True, partial_answer="Harbor"
            )
        return CriticResult(action=CriticAction.GIVE_UP)

    _plan(monkeypatch, FIRST, UNRESOLVED, LAST)
    _script_critic(monkeypatch, verdict)

    chain = _run(_executor(monkeypatch))

    assert chain.steps[0].critic_action == CriticAction.SUFFICIENT
    assert chain.steps[0].conclusion == "Harbor"


def test_skip_spends_no_hop(monkeypatch) -> None:
    """Two hops are enough for two executed nodes however many skips sit between them."""
    _plan(monkeypatch, FIRST, FIRST, FIRST, LAST)
    _script_critic(monkeypatch)

    chain = _run(_executor(monkeypatch), max_hops=2)

    assert [(s.sub_question, s.hop) for s in chain.steps] == [(FIRST, 1), (LAST, 2)]


def test_run_ends_without_a_critic_when_only_skips_remain(monkeypatch) -> None:
    _plan(monkeypatch, FIRST, "Who owns {from:sq1}?", "Who runs {from:sq2}?")
    judged = _script_critic(monkeypatch)

    chain = _run(_executor(monkeypatch))

    assert [s.sub_question for s in chain.steps] == [FIRST]
    assert judged == [FIRST]
    assert [s.hop for s in chain.steps] == [1]


def test_skipped_nodes_are_reported_with_a_reason(monkeypatch) -> None:
    _plan(monkeypatch, FIRST, FIRST, UNRESOLVED, LAST)
    _script_critic(monkeypatch)

    chain = _run(_executor(monkeypatch))

    assert chain.metadata["skipped_sub_questions"] == [
        {"id": "sq2", "text": FIRST, "reason": "duplicate"},
        {"id": "sq3", "text": UNRESOLVED, "reason": "unresolved_dependency"},
    ]
    # Skipped nodes are not "planned but never reached".
    assert "unexecuted_sub_questions" not in chain.metadata


def test_prefetched_sibling_after_a_skip_stays_on_the_same_hop(monkeypatch) -> None:
    _plan(monkeypatch, FIRST, UNRESOLVED, LAST)
    judged = _script_critic(monkeypatch)

    chain = _run(_executor(monkeypatch), parallel_branches=True)

    assert [(s.sub_question, s.hop) for s in chain.steps] == [(FIRST, 1), (LAST, 1)]
    assert judged == [FIRST, LAST]


def _stream_frames(executor: Executor) -> list[tuple[str, dict]]:
    options = AgentRunOptions(
        guard_cfg=GuardrailConfig(max_hops=4, max_sub_questions=4, parallel_branches=False),
        allow_llm=False,
    )
    return [
        (event, payload)
        for event, payload in iter_agentic(QUESTION, executor, None, run_opts=options)
        if event in {StreamEvent.SUB_QUESTION, StreamEvent.HOP_DONE}
    ]


def test_stream_has_one_frame_pair_per_executed_step(monkeypatch) -> None:
    _plan(monkeypatch, FIRST, UNRESOLVED, LAST)
    _script_critic(monkeypatch)

    frames = _stream_frames(_executor(monkeypatch))

    assert [(e, p["hop"]) for e, p in frames] == [
        (StreamEvent.SUB_QUESTION, 1),
        (StreamEvent.HOP_DONE, 1),
        (StreamEvent.SUB_QUESTION, 2),
        (StreamEvent.HOP_DONE, 2),
    ]


def test_stream_does_not_replay_last_step_when_only_skips_remain(monkeypatch) -> None:
    _plan(monkeypatch, FIRST, UNRESOLVED)
    _script_critic(monkeypatch)

    frames = _stream_frames(_executor(monkeypatch))

    assert [e for e, _ in frames] == [StreamEvent.SUB_QUESTION, StreamEvent.HOP_DONE]


def test_terminal_executor_update_emits_no_step_frames() -> None:
    chain = ReasoningChain(question=QUESTION)
    chain.steps.append(ReasoningStep(hop=1, sub_question=FIRST))
    assert list(events_for_node(AgentNode.EXECUTOR, {"chain": chain, "done": True})) == []
    frames = list(events_for_node(AgentNode.EXECUTOR, {"chain": chain, "done": False}))
    assert [event for event, _ in frames] == [StreamEvent.SUB_QUESTION]


def test_trip_keeps_the_advanced_plan_cursor(monkeypatch) -> None:
    _plan(monkeypatch, UNRESOLVED, LAST)
    _script_critic(monkeypatch)
    guard = GuardrailConfig(max_hops=4, max_sub_questions=4, parallel_branches=False)
    budget = guard.budget_tracker()
    budget.llm_calls = guard.max_llm_calls  # Exhausted before the first hop.
    graph = build_graph(_executor(monkeypatch), None, guard, budget=budget)
    state = initial_state(QUESTION, ReasoningChain(question=QUESTION), allow_llm=False)

    result = graph.invoke(state, invoke_config("skip-trip"))

    assert result["done"] is True
    assert result["current_index"] == 1
    assert result["chain"].steps == []
    assert "tripped" in result["guardrail_status"]


def test_all_skipped_nodes_still_check_the_budget_without_spending_a_hop(monkeypatch) -> None:
    _plan(monkeypatch, UNRESOLVED)
    guard = GuardrailConfig(max_hops=4, max_sub_questions=4, parallel_branches=False)
    budget = guard.budget_tracker()
    budget.llm_calls = guard.max_llm_calls
    graph = build_graph(_executor(monkeypatch), None, guard, budget=budget)
    state = initial_state(QUESTION, ReasoningChain(question=QUESTION), allow_llm=False)

    result = graph.invoke(state, invoke_config("all-skipped-trip"))

    assert result["hop"] == 0
    assert result["chain"].steps == []
    assert "tripped" in result["guardrail_status"]
