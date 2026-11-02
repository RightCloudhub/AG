"""Typed checkpoint codecs preserve domain models, including asynchronous stores."""

from dataclasses import replace

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Overwrite

from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.plan_dag import SubQuestion
from agentic_graphrag.agent.register_state import RegisterRuntimeState
from agentic_graphrag.agent.requirements import Register, Requirement
from agentic_graphrag.agent.state import STATE_SCHEMA_VERSION
from agentic_graphrag.agent.state_checkpointer import StateCheckpointer
from agentic_graphrag.agent.state_serde import AgentStateSerializer
from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.reasoning import ReasoningChain


def test_model_containers_roundtrip_only_at_the_checkpoint_boundary() -> None:
    candidate = Candidate(id="c1", source=CandidateSource.FULLTEXT_CHUNK, content="evidence")
    memory = MemoryState()
    memory.add_evidence([candidate])
    memory.mark_subquestion_done("sq1", "conclusion")
    register = Register(question="q", items={"r1": Requirement(text="need", passages={"c1"})})
    runtime = RegisterRuntimeState(
        cache={"query": ["c1"]},
        seen=["c1"],
        events=[
            {
                "type": "initialization",
                "round": 0,
                "error": "",
                "register": register,
                "seed_ids": ["c1"],
            }
        ],
    )
    values = [
        ReasoningChain(question="q"),
        memory,
        register,
        runtime,
        {"c1": candidate},
        [SubQuestion(id="sq1", text="question")],
        {"sq2": ([candidate], [])},
        Overwrite({"c1": candidate}),
        Overwrite(runtime),
    ]
    serializer = AgentStateSerializer()
    for value in values:
        encoded = serializer.dumps_typed(value)
        assert encoded[0].startswith("agent-state/3/")
        restored = serializer.loads_typed(encoded)
        assert type(restored) is type(value) and restored == value
    encoded = serializer.dumps_typed(runtime)
    runtime.seen.append("later")
    assert serializer.loads_typed(encoded) == replace(runtime, seen=["c1"])


def test_plain_langgraph_values_keep_the_default_codec() -> None:
    serializer = AgentStateSerializer()
    default = JsonPlusSerializer()
    for value in ({}, [], None, "plain", 5, {"ordinary": "value"}, ("mode", {})):
        assert serializer.loads_typed(serializer.dumps_typed(value)) == default.loads_typed(
            default.dumps_typed(value)
        )


async def test_async_checkpoint_operations_keep_typed_state_and_history() -> None:
    saver = StateCheckpointer(MemorySaver())
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {
        "state_version": STATE_SCHEMA_VERSION,
        "chain": ReasoningChain(question="q"),
    }
    checkpoint["channel_versions"] = {"state_version": "1", "chain": "1"}
    config = {"configurable": {"thread_id": "async-thread", "checkpoint_ns": ""}}
    written = await saver.aput(config, checkpoint, {}, new_versions=checkpoint["channel_versions"])
    await saver.aput_writes(written, [("done", True)], "task", task_path="node")
    saved = await saver.aget_tuple(config)
    assert saved is not None and isinstance(
        saved.checkpoint["channel_values"]["chain"], ReasoningChain
    )
    assert saved.pending_writes == [("task", "done", True)]
    assert len([saved async for saved in saver.alist(config)]) == 1
    await saver.adelete_thread("async-thread")
    assert await saver.aget_tuple(config) is None


def test_custom_backend_serializer_is_preserved() -> None:
    serializer = AgentStateSerializer()
    backend = MemorySaver(serde=serializer)
    saver = StateCheckpointer(backend)
    assert saver.serde is serializer and backend.serde is serializer
