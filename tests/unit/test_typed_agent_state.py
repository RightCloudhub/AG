"""Typed node state, reducer behavior and copy-on-write checkpoint isolation."""

from dataclasses import replace
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agentic_graphrag.agent.critic import CriticResult
from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import build_graph, invoke_config, run_agentic_query
from agentic_graphrag.agent.loop_runtime import AgentRuntime
from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.parallel_branches import park, take
from agentic_graphrag.agent.plan_dag import SubQuestion
from agentic_graphrag.agent.register_state import (
    ProgressEvent,
    RegisterRuntimeState,
    append_events,
    merge_register_runtime,
)
from agentic_graphrag.agent.requirements import Register
from agentic_graphrag.agent.state import AgentState, initial_state, merge_evidence
from agentic_graphrag.core.enums import CriticAction, RetrievalTool
from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.reasoning import ReasoningChain, ToolCallTrace
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore


def candidate(cid: str = "c1", text: str = "Elena Varga leads Apex Holdings") -> Candidate:
    return Candidate(id=cid, source=CandidateSource.FULLTEXT_CHUNK, content=text)


def progress(round_number: int) -> ProgressEvent:
    return {
        "type": "progress",
        "round": round_number,
        "changes": [],
        "verifier": [],
        "new_passages": [],
    }


def test_nodes_keep_objects_without_validation_or_dump_roundtrips(monkeypatch) -> None:
    hit = candidate()
    executor = Executor(GraphRetriever(InMemoryGraphStore()))
    monkeypatch.setattr(executor, "run", lambda *_args, **_kwargs: ([hit], []))
    monkeypatch.setattr(
        "agentic_graphrag.agent.loop_runtime.critique",
        lambda *_args, **_kwargs: CriticResult(
            action=CriticAction.SUFFICIENT, partial_answer="Elena Varga"
        ),
    )
    state = initial_state("Who leads Apex Holdings?", ReasoningChain(question="q"), allow_llm=False)
    state["sub_questions"] = [SubQuestion(id="sq1", text=state["question"])]
    runtime = AgentRuntime(executor, None, GuardrailConfig(max_hops=4))
    with (
        patch.object(
            ReasoningChain, "model_validate", side_effect=AssertionError("validate chain")
        ),
        patch.object(ReasoningChain, "model_dump", side_effect=AssertionError("dump chain")),
        patch.object(Candidate, "model_validate", side_effect=AssertionError("validate evidence")),
        patch.object(Candidate, "model_dump", side_effect=AssertionError("dump evidence")),
        patch.object(Register, "model_validate", side_effect=AssertionError("validate register")),
    ):
        executed = runtime.node_executor(state)
        critiqued = runtime.node_critic(executed)
    assert executed["evidence"]["c1"] is hit
    assert executed["memory"].evidence["c1"] is hit
    assert not state["chain"].steps and not state["memory"].evidence
    assert executed["chain"].steps[-1].conclusion == ""
    assert critiqued["chain"].steps[-1].conclusion == "Elena Varga"
    assert not executed["memory"].conclusions
    assert critiqued["memory"].conclusions == ["Elena Varga"]


def test_parallel_reducer_preserves_first_hit_and_both_branch_updates() -> None:
    original, duplicate, second = candidate(), candidate(text="replacement"), candidate("c2")
    graph = StateGraph(AgentState)
    graph.add_node("left", lambda _state: {"evidence": {"c1": duplicate}})
    graph.add_node("right", lambda _state: {"evidence": {"c2": second}})
    for node in ("left", "right"):
        graph.add_edge(START, node)
        graph.add_edge(node, END)
    result = graph.compile().invoke({"evidence": {"c1": original}})
    assert list(result["evidence"]) == ["c1", "c2"]
    assert result["evidence"]["c1"] is original
    assert result["evidence"]["c2"] is second
    assert merge_evidence({"c1": original}, {}) == {"c1": original}


def test_register_events_append_once_for_full_snapshots_and_deltas() -> None:
    first, second, third = progress(1), progress(2), progress(3)
    previous = RegisterRuntimeState(events=[first], seen=["c1"])
    incoming = replace(previous, events=[first, second], progress="approved progress")
    merged = merge_register_runtime(previous, incoming)
    assert merged.events == [first, second]
    assert merged.progress == incoming.progress and merged.seen == ["c1"]
    assert append_events(merged.events, [third, third]) == [first, second, third]
    assert previous.events == [first] and incoming.events == [first, second]


def test_prefetched_candidates_and_traces_are_not_serialized() -> None:
    hit = candidate()
    trace = ToolCallTrace(tool=RetrievalTool.FULLTEXT_SEARCH, hits=[hit.id])
    stored = park({}, {"sq2": ([hit], [trace])})
    result, remaining = take(stored, "sq2")
    assert result is not None and result[0][0] is hit and result[1][0] is trace
    assert not remaining and "sq2" in stored
    assert take(remaining, "missing") == (None, {})


def test_initial_states_do_not_share_mutable_substates() -> None:
    left = initial_state("q", ReasoningChain(question="q"), allow_llm=False)
    right = initial_state("q", ReasoningChain(question="q"), allow_llm=False)
    left["memory"].exclude_hypothesis("bad path")
    left["register_runtime"].cache["search"] = ["c1"]
    assert not right["memory"].excluded_hypotheses and not right["register_runtime"].cache
    assert isinstance(left["memory"], MemoryState)


def test_fresh_query_on_existing_thread_resets_append_channels(monkeypatch) -> None:
    hits = iter([candidate("first"), candidate("second")])
    executor = Executor(GraphRetriever(InMemoryGraphStore()))
    monkeypatch.setattr(executor, "run", lambda *_args, **_kwargs: ([next(hits)], []))
    backend = MemorySaver()
    for question in ("Question about first company", "Question about second company"):
        run_agentic_query(
            question,
            executor,
            None,
            allow_llm=False,
            guard_cfg=GuardrailConfig(max_hops=1),
            checkpointer=backend,
            thread_id="shared-thread",
        )
    saved = backend.get_tuple(invoke_config("shared-thread"))
    assert saved is not None
    assert list(saved.checkpoint["channel_values"]["evidence"]) == ["second"]
    assert list(saved.checkpoint["channel_values"]["memory"].evidence) == ["second"]


def test_initial_state_sets_every_channel() -> None:
    """A channel left out here is inherited from the previous run on a reused thread."""
    state = initial_state("q", ReasoningChain(question="q"), allow_llm=False)
    assert set(state) == set(AgentState.__annotations__)


def test_fresh_query_on_existing_thread_ignores_previous_trip(monkeypatch) -> None:
    executor = Executor(GraphRetriever(InMemoryGraphStore()))
    monkeypatch.setattr(executor, "run", lambda *_args, **_kwargs: ([candidate("fresh")], []))
    backend = MemorySaver()
    tripped = run_agentic_query(
        "Who leads Apex Holdings?",
        executor,
        None,
        allow_llm=False,
        guard_cfg=GuardrailConfig(max_hops=1, max_llm_calls=0),
        checkpointer=backend,
        thread_id="shared-thread",
    )
    assert not tripped.steps

    def ask(thread_id: str) -> ReasoningChain:
        return run_agentic_query(
            "Who leads Apex Holdings?",
            executor,
            None,
            allow_llm=False,
            guard_cfg=GuardrailConfig(max_hops=1),
            checkpointer=backend,
            thread_id=thread_id,
        )

    reused, fresh = ask("shared-thread"), ask("fresh-thread")
    assert reused.steps and reused.status == fresh.status and reused.answer == fresh.answer


def test_fresh_query_on_existing_thread_drops_parked_branches(monkeypatch) -> None:
    executor = Executor(GraphRetriever(InMemoryGraphStore()))
    monkeypatch.setattr(executor, "run", lambda *_args, **_kwargs: ([candidate("fresh")], []))
    backend = MemorySaver()
    question = "Who leads Apex Holdings?"
    graph = build_graph(executor, None, GuardrailConfig(max_hops=1), checkpointer=backend)
    config = invoke_config("shared-thread")
    parked = {"sq9": ([candidate("stale", "Evidence for an earlier question")], [])}
    graph.invoke(
        {
            **initial_state(question, ReasoningChain(question=question), allow_llm=False),
            "prefetched": parked,
        },
        config,
    )
    saved = backend.get_tuple(config)
    assert saved is not None and "sq9" in saved.checkpoint["channel_values"]["prefetched"]

    run_agentic_query(
        question,
        executor,
        None,
        allow_llm=False,
        guard_cfg=GuardrailConfig(max_hops=1),
        checkpointer=backend,
        thread_id="shared-thread",
    )

    saved = backend.get_tuple(config)
    assert saved is not None and saved.checkpoint["channel_values"]["prefetched"] == {}


@pytest.mark.parametrize("hits", [[], [candidate("fresh")], [candidate("seed", "replacement")]])
def test_executor_replaces_inherited_seed_pool_like_legacy_nodes(monkeypatch, hits) -> None:
    seed = candidate("seed", "inherited evidence")
    executor = Executor(GraphRetriever(InMemoryGraphStore()))
    monkeypatch.setattr(executor, "run", lambda *_args, **_kwargs: (hits, []))
    question = "Who leads Apex Holdings?"
    graph = build_graph(executor, None, GuardrailConfig(max_hops=1))
    state = initial_state(
        question, ReasoningChain(question=question), allow_llm=False, evidence=[seed]
    )
    result = graph.invoke(state, invoke_config("seed-replacement"))
    expected = {hit.id: hit for hit in hits}
    assert result["evidence"] == expected
    assert result["memory"].evidence == expected


def test_register_is_required_only_for_register_nodes() -> None:
    from agentic_graphrag.agent.state import active_register

    with pytest.raises(ValueError, match="register planner"):
        active_register(initial_state("q", ReasoningChain(question="q"), allow_llm=False))
