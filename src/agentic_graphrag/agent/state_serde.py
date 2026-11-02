"""Checkpoint codecs for typed state values, with no dynamic model imports."""

from __future__ import annotations

from typing import cast

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Overwrite
from pydantic import TypeAdapter

from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.plan_dag import SubQuestion
from agentic_graphrag.agent.register_state import RegisterRuntimeState
from agentic_graphrag.agent.requirements import Register
from agentic_graphrag.agent.state import Retrieved
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ReasoningChain, ToolCallTrace

_PREFIX = "agent-state/3/"
_OVERWRITE_TAG = "overwrite/"
_CODECS = {
    "chain": TypeAdapter[object](ReasoningChain),
    "evidence": TypeAdapter[object](dict[str, Candidate]),
    "memory": TypeAdapter[object](MemoryState),
    "register": TypeAdapter[object](Register),
    "register_runtime": TypeAdapter[object](RegisterRuntimeState),
    "sub_questions": TypeAdapter[object](list[SubQuestion]),
    "prefetched": TypeAdapter[object](dict[str, Retrieved]),
}
_MODEL_TAGS = {
    ReasoningChain: "chain",
    MemoryState: "memory",
    Register: "register",
    RegisterRuntimeState: "register_runtime",
}


class AgentStateSerializer(JsonPlusSerializer):
    """Bulk-serialize model containers only when the checkpointer writes bytes."""

    def dumps_typed(self, obj: object) -> tuple[str, bytes]:
        value = obj.value if isinstance(obj, Overwrite) else obj
        tag = _tag(value)
        if tag is not None:
            prefix = _PREFIX + _OVERWRITE_TAG if isinstance(obj, Overwrite) else _PREFIX
            return prefix + tag, _CODECS[tag].dump_json(value)
        return super().dumps_typed(obj)

    def loads_typed(self, data: tuple[str, bytes]) -> object:
        tag, payload = data
        if tag.startswith(_PREFIX):
            kind = tag.removeprefix(_PREFIX)
            value = _CODECS[kind.removeprefix(_OVERWRITE_TAG)].validate_json(payload)
            return Overwrite(value) if kind.startswith(_OVERWRITE_TAG) else value
        return cast(object, super().loads_typed(data))


def _tag(value: object) -> str | None:
    tag = _MODEL_TAGS.get(type(value))
    if tag is not None:
        return tag
    if isinstance(value, list):
        return "sub_questions" if value and all(isinstance(v, SubQuestion) for v in value) else None
    if isinstance(value, dict) and value:
        return _mapping_tag(value)
    return None


def _mapping_tag(value: dict[str, object]) -> str | None:
    if all(isinstance(v, Candidate) for v in value.values()):
        return "evidence"
    return "prefetched" if all(_is_retrieved(v) for v in value.values()) else None


def _is_retrieved(value: object) -> bool:
    if not isinstance(value, tuple) or len(value) != 2:
        return False
    candidates, traces = value
    return (
        isinstance(candidates, list)
        and isinstance(traces, list)
        and all(isinstance(c, Candidate) for c in candidates)
        and all(isinstance(t, ToolCallTrace) for t in traces)
    )
