"""Legacy checkpoints migrate models, runtime channels and interrupted writes."""

from typing import TypedDict

import pytest
from langgraph.checkpoint.base import CheckpointTuple, empty_checkpoint
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Overwrite
from pydantic import TypeAdapter

from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import build_graph, invoke_config
from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.plan_dag import SubQuestion
from agentic_graphrag.agent.register_state import RegisterRuntimeState
from agentic_graphrag.agent.requirements import Register, Requirement
from agentic_graphrag.agent.state import STATE_SCHEMA_VERSION, AgentState, initial_state
from agentic_graphrag.agent.state_checkpointer import StateCheckpointer
from agentic_graphrag.agent.state_migration import migrate_checkpoint, migrate_state
from agentic_graphrag.core.enums import RequirementStatus, RetrievalTool
from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.ports.stores import EntityRecord, RelationRecord
from agentic_graphrag.core.reasoning import ReasoningChain, ToolCallTrace
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

QUESTION = "Who is the CEO of Apex Holdings?"


class LegacyState(TypedDict, total=False):
    state_version: int
    question: str
    chain: dict[str, object]
    evidence: list[dict[str, object]]
    memory_snapshot: dict[str, object]
    sub_questions: list[dict[str, object]]
    hop: int
    current_index: int
    allow_llm: bool
    tenant_id: str
    done: bool


def legacy_state() -> LegacyState:
    hit = Candidate(
        id="c1",
        source=CandidateSource.FULLTEXT_CHUNK,
        content="Elena Varga is the CEO of Apex Holdings.",
    )
    memory = MemoryState()
    memory.add_evidence([hit])
    memory.exclude_hypothesis("bad path")
    memory.mark_subquestion_done("upstream", "Apex Holdings")
    return {
        "state_version": 2,
        "question": QUESTION,
        "chain": ReasoningChain(question=QUESTION).model_dump(),
        "evidence": [hit.model_dump()],
        "memory_snapshot": dict(memory.to_snapshot()),
        "sub_questions": [SubQuestion(id="sq1", text=QUESTION).model_dump()],
        "hop": 0,
        "current_index": 0,
        "allow_llm": False,
        "tenant_id": "",
        "done": False,
    }


def legacy_register_payload() -> dict[str, object]:
    payload: dict[str, object] = dict(legacy_state())
    register = Register(
        question=QUESTION,
        items={"sq1": Requirement(req_id="sq1", text=QUESTION, status=RequirementStatus.ACTIVE)},
    )
    trace = ToolCallTrace(tool=RetrievalTool.FULLTEXT_SEARCH, hits=["c1"])
    payload.update(
        requirement_register=register.snapshot(),
        register_context=[{"role": "user", "content": "approved context"}],
        register_events=[
            {
                "type": "initialization",
                "round": 0,
                "error": "",
                "register": register.snapshot(),
                "seed_ids": ["c1"],
                "evidence_summary": "seed summary",
                "reasoning_note": "two needs",
            }
        ],
        register_cache={"fulltext:key": ["c1"]},
        register_seen=["c1"],
        register_attempts={"sq1": 2},
        register_fresh=["c1"],
        register_stop="round_budget",
        register_progress="approved progress",
        register_answer_policy="abstain",
        prefetched={"sq2": {"candidates": payload["evidence"], "traces": [trace.model_dump()]}},
    )
    return payload


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_snapshots_hydrate_all_typed_substates_once(version: int) -> None:
    payload = legacy_register_payload()
    payload["state_version"] = version
    state = migrate_state(payload)
    assert state["state_version"] == STATE_SCHEMA_VERSION
    assert isinstance(state["chain"], ReasoningChain)
    assert isinstance(state["sub_questions"][0], SubQuestion)
    assert isinstance(state["register"], Register)
    assert isinstance(state["memory"], MemoryState)
    assert isinstance(state["evidence"]["c1"], Candidate)
    assert state["memory"].is_excluded("BAD PATH")
    assert state["memory"].conclusions_by_subquestion == {"upstream": "Apex Holdings"}
    runtime = state["register_runtime"]
    assert isinstance(runtime, RegisterRuntimeState)
    assert runtime.cache == {"fulltext:key": ["c1"]}
    assert runtime.attempts == {"sq1": 2} and runtime.seen == runtime.fresh == ["c1"]
    assert runtime.stop == "round_budget" and runtime.answer_policy == "abstain"
    assert runtime.progress == "approved progress" and runtime.context[0]["content"]
    event = runtime.events[0]
    assert event["type"] == "initialization" and isinstance(event["register"], Register)
    candidates, traces = state["prefetched"]["sq2"]
    assert isinstance(candidates[0], Candidate) and isinstance(traces[0], ToolCallTrace)
    assert payload["state_version"] == version and "memory_snapshot" in payload


def test_versionless_snapshots_and_future_versions() -> None:
    payload = dict(legacy_state())
    payload.pop("state_version")
    assert migrate_state(payload)["state_version"] == STATE_SCHEMA_VERSION
    payload["state_version"] = STATE_SCHEMA_VERSION + 1
    with pytest.raises(ValueError, match="Unsupported agent state_version"):
        migrate_state(payload)


def test_checkpoint_versions_and_pending_register_writes_migrate_together() -> None:
    checkpoint = empty_checkpoint()
    payload = legacy_register_payload()
    checkpoint["channel_values"] = payload
    checkpoint["channel_versions"] = {
        "register_cache": 10,
        "register_events": 2,
        "memory_snapshot": 4,
        "requirement_register": 5,
    }
    checkpoint["versions_seen"] = {"critic": dict(checkpoint["channel_versions"])}
    pending = [("task", key, value) for key, value in payload.items()]
    pending.append(("error-task", "__error__", "tool failed"))
    saved = CheckpointTuple(invoke_config("legacy"), checkpoint, {}, pending_writes=pending)
    migrated = migrate_checkpoint(saved)
    assert migrated is not None
    assert migrated.checkpoint["channel_versions"] == {
        "register_runtime": 10,
        "memory": 4,
        "register": 5,
    }
    assert migrated.checkpoint["versions_seen"]["critic"]["register_runtime"] == 10
    writes = {(task, channel): value for task, channel, value in migrated.pending_writes}
    assert isinstance(writes["task", "register_runtime"], RegisterRuntimeState)
    assert isinstance(writes["task", "chain"], ReasoningChain)
    assert writes["error-task", "__error__"] == "tool failed"
    assert ("error-task", "state_version") not in writes
    assert "register_cache" in saved.checkpoint["channel_versions"]


def test_old_checkpoint_resumes_with_fresh_typed_graph_and_runtime() -> None:
    backend = MemorySaver()
    old = StateGraph(LegacyState)
    old.add_node("planner", lambda state: state)
    old.add_node("executor", lambda state: state)
    old.set_entry_point("planner")
    old.add_edge("planner", "executor")
    old.add_edge("executor", END)
    config = invoke_config("old-interrupted-query")
    old.compile(checkpointer=backend, interrupt_after=["planner"]).invoke(legacy_state(), config)
    store = InMemoryGraphStore()
    store.upsert_entities(
        [
            EntityRecord(id="person", name="Elena Varga", type="Person"),
            EntityRecord(id="company", name="Apex Holdings", type="Company"),
        ]
    )
    store.upsert_relations(
        [
            RelationRecord(
                id="ceo",
                type="CEO_OF",
                head_id="person",
                tail_id="company",
                head_name="Elena Varga",
                tail_name="Apex Holdings",
            )
        ]
    )
    executor = Executor(GraphRetriever(store), known_entities=["Apex Holdings", "Elena Varga"])
    graph = build_graph(executor, None, GuardrailConfig(max_hops=4), checkpointer=backend)
    before = graph.get_state(config)
    assert before.next == ("executor",)
    assert isinstance(before.values["chain"], ReasoningChain)
    assert before.values["memory"].is_excluded("bad path")
    result = graph.invoke(None, config)
    assert result["state_version"] == STATE_SCHEMA_VERSION and result["done"]
    assert "Elena Varga" in result["chain"].answer and len(result["chain"].steps) == 1
    assert result["memory"].conclusions_by_subquestion["upstream"] == "Apex Holdings"
    assert all(
        isinstance(s.values.get("chain"), ReasoningChain)
        for s in graph.get_state_history(config)
        if s.values.get("chain")
    )


def test_checkpointer_rejects_future_state_version_on_load() -> None:
    backend = MemorySaver()
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"state_version": STATE_SCHEMA_VERSION + 1}
    checkpoint["channel_versions"] = {"state_version": "1"}
    config = {"configurable": {"thread_id": "future", "checkpoint_ns": ""}}
    backend.put(config, checkpoint, {}, checkpoint["channel_versions"])
    with pytest.raises(ValueError, match="Unsupported agent state_version"):
        StateCheckpointer(backend).get_tuple(config)


def test_partial_typed_writes_use_the_checkpoint_state_version() -> None:
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"state_version": STATE_SCHEMA_VERSION}
    hit = Candidate(id="c1", source=CandidateSource.FULLTEXT_CHUNK, content="evidence")
    pending = [("partial", "evidence", {"c1": hit})]
    saved = CheckpointTuple(invoke_config("partial"), checkpoint, {}, pending_writes=pending)
    migrated = migrate_checkpoint(saved)
    assert migrated is not None and len(migrated.pending_writes) == 1
    assert migrated.pending_writes[0][2]["c1"] is hit


def test_starting_checkpoint_preserves_fresh_query_overwrites() -> None:
    state = initial_state(QUESTION, ReasoningChain(question=QUESTION), allow_llm=False)
    graph_input = {**state, "evidence": Overwrite(state["evidence"])}
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"__start__": graph_input}
    saved = CheckpointTuple(invoke_config("start"), checkpoint, {})
    migrated = migrate_checkpoint(saved)
    assert migrated is not None
    assert isinstance(migrated.checkpoint["channel_values"]["__start__"]["evidence"], Overwrite)


def test_pending_fresh_query_writes_preserve_both_append_channel_overwrites() -> None:
    state = initial_state(QUESTION, ReasoningChain(question=QUESTION), allow_llm=False)
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"state_version": STATE_SCHEMA_VERSION}
    graph_input = {
        **state,
        "evidence": Overwrite(state["evidence"]),
        "register_runtime": Overwrite(state["register_runtime"]),
    }
    pending = [("input", channel, value) for channel, value in graph_input.items()]
    saved = CheckpointTuple(invoke_config("pending-start"), checkpoint, {}, pending_writes=pending)
    migrated = migrate_checkpoint(saved)
    assert migrated is not None
    writes = {channel: value for _, channel, value in migrated.pending_writes}
    for channel in ("evidence", "register_runtime"):
        assert isinstance(writes[channel], Overwrite)
        assert writes[channel].value == state[channel]


def test_current_starting_checkpoint_hydrates_wire_models_and_overwrites() -> None:
    hit = Candidate(id="c1", source=CandidateSource.FULLTEXT_CHUNK, content="evidence")
    state = initial_state(
        QUESTION, ReasoningChain(question=QUESTION), allow_llm=False, evidence=[hit]
    )
    wire = TypeAdapter(AgentState).dump_python(state, mode="json")
    for channel in ("evidence", "register_runtime"):
        wire[channel] = Overwrite(wire[channel])
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"__start__": wire}
    saved = CheckpointTuple(invoke_config("current-start"), checkpoint, {})
    migrated = migrate_checkpoint(saved)
    assert migrated is not None
    restored = migrated.checkpoint["channel_values"]["__start__"]
    assert isinstance(restored["chain"], ReasoningChain)
    assert isinstance(restored["memory"], MemoryState)
    assert isinstance(restored["evidence"], Overwrite)
    assert isinstance(restored["evidence"].value["c1"], Candidate)
    assert isinstance(restored["register_runtime"], Overwrite)
    assert isinstance(restored["register_runtime"].value, RegisterRuntimeState)
