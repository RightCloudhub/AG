"""Upgrade persisted v1/v2 channels and pending writes to typed AgentState v3."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict, cast

from langgraph.checkpoint.base import ChannelVersions, CheckpointTuple
from langgraph.types import Overwrite
from pydantic import TypeAdapter

from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.register_state import RegisterRuntimeState
from agentic_graphrag.agent.state import STATE_SCHEMA_VERSION, AgentState
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ToolCallTrace

_STATE_ADAPTER = TypeAdapter(AgentState)
_EVIDENCE_ADAPTER = TypeAdapter(list[Candidate])
_LEGACY_RUNTIME_KEYS = (
    "register_context",
    "register_events",
    "register_cache",
    "register_seen",
    "register_attempts",
    "register_fresh",
    "register_stop",
    "register_progress",
    "register_answer_policy",
)
_RENAMED_CHANNELS = {
    "memory_snapshot": "memory",
    "requirement_register": "register",
    **{key: "register_runtime" for key in _LEGACY_RUNTIME_KEYS},
}


class _LegacyPrefetch(TypedDict):
    candidates: list[Candidate]
    traces: list[ToolCallTrace]


_PREFETCH_ADAPTER = TypeAdapter(dict[str, _LegacyPrefetch])


def migrate_state(payload: Mapping[str, object]) -> AgentState:
    """Validate once at the checkpoint boundary; never hydrate inside nodes."""
    version = payload.get("state_version", 1)
    if not isinstance(version, int) or not 1 <= version <= STATE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported agent state_version: {version!r}")
    data = dict(payload)
    if version < STATE_SCHEMA_VERSION:
        data = _upgrade_legacy(data)
    return _STATE_ADAPTER.validate_python(data)


def _upgrade_legacy(data: dict[str, object]) -> dict[str, object]:
    data["state_version"] = STATE_SCHEMA_VERSION
    if "evidence" in data:
        data["evidence"] = _legacy_evidence(data["evidence"])
    if "memory_snapshot" in data:
        data["memory"] = MemoryState.from_snapshot(_mapping(data.pop("memory_snapshot")))
    if "requirement_register" in data:
        data["register"] = data.pop("requirement_register")
    _upgrade_runtime(data)
    if "prefetched" in data:
        prefetched = _PREFETCH_ADAPTER.validate_python(data["prefetched"])
        data["prefetched"] = {
            key: (result["candidates"], result["traces"]) for key, result in prefetched.items()
        }
    if "question" in data:
        data.setdefault("memory", MemoryState())
        data.setdefault("register", None)
        data.setdefault("register_runtime", RegisterRuntimeState())
    return data


def _legacy_evidence(value: object) -> dict[str, Candidate]:
    evidence: dict[str, Candidate] = {}
    for candidate in _EVIDENCE_ADAPTER.validate_python(value):
        evidence.setdefault(candidate.id, candidate)
    return evidence


def _upgrade_runtime(data: dict[str, object]) -> None:
    runtime = {
        key.removeprefix("register_"): data.pop(key) for key in _LEGACY_RUNTIME_KEYS if key in data
    }
    if runtime:
        data["register_runtime"] = runtime


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError("agent checkpoint memory must be an object")
    return cast(dict[str, object], value)


def migrate_checkpoint(saved: CheckpointTuple | None) -> CheckpointTuple | None:
    if saved is None:
        return None
    checkpoint = saved.checkpoint.copy()
    values: dict[str, object] = checkpoint["channel_values"]
    if "chain" in values or "state_version" in values:
        checkpoint["channel_values"] = _migrate_channels(values)
        checkpoint["channel_versions"] = _migrate_versions(checkpoint["channel_versions"])
        checkpoint["versions_seen"] = {
            node: _migrate_versions(versions)
            for node, versions in checkpoint["versions_seen"].items()
        }
    elif "__start__" in values:
        checkpoint["channel_values"] = {
            **values,
            "__start__": _migrate_input(values["__start__"]),
        }
    version = _snapshot_version(values)
    return saved._replace(
        checkpoint=checkpoint,
        pending_writes=_migrate_writes(saved.pending_writes or [], version=version),
    )


def _migrate_channels(values: Mapping[str, object], *, version: int = 1) -> dict[str, object]:
    result = dict(values)
    if "__start__" in result:
        result["__start__"] = _migrate_input(result["__start__"])
    if not any(key in values for key in AgentState.__annotations__ | _RENAMED_CHANNELS):
        return result
    payload = {
        key: value.value if isinstance(value, Overwrite) else value for key, value in values.items()
    }
    state = migrate_state({"state_version": version, **payload})
    if version == STATE_SCHEMA_VERSION and "state_version" not in values:
        state.pop("state_version", None)
    for key in _RENAMED_CHANNELS:
        result.pop(key, None)
    result.update(
        {
            key: Overwrite(value) if isinstance(values.get(key), Overwrite) else value
            for key, value in state.items()
        }
    )
    return result


def _migrate_input(value: object) -> dict[str, object]:
    payload = _mapping(value)
    migrated = _migrate_channels(payload)
    if payload.get("state_version") == STATE_SCHEMA_VERSION:
        return migrated
    for channel in ("evidence", "register_runtime"):
        if channel in migrated:
            migrated[channel] = Overwrite(migrated[channel])
    return migrated


def _snapshot_version(values: Mapping[str, object]) -> int:
    payload = _mapping(values["__start__"]) if "__start__" in values else values
    version = payload.get("state_version", 1)
    if not isinstance(version, int) or not 1 <= version <= STATE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported agent state_version: {version!r}")
    return version


def _migrate_versions(versions: ChannelVersions) -> ChannelVersions:
    result: ChannelVersions = {}
    for key, version in versions.items():
        renamed = _RENAMED_CHANNELS.get(key, key)
        previous = result.get(renamed)
        if previous is None or _newer_version(version, previous):
            result[renamed] = version
    return result


def _newer_version(version: str | int | float, previous: str | int | float) -> bool:
    if isinstance(version, str) and isinstance(previous, str):
        return version > previous
    if isinstance(version, (int, float)) and isinstance(previous, (int, float)):
        return version > previous
    raise ValueError("checkpoint channel versions must use one comparable format")


def _migrate_writes(
    writes: list[tuple[str, str, object]], *, version: int
) -> list[tuple[str, str, object]]:
    tasks: dict[str, dict[str, object]] = {}
    for task_id, channel, value in writes:
        tasks.setdefault(task_id, {})[channel] = value
    result: list[tuple[str, str, object]] = []
    for task_id, values in tasks.items():
        migrated = _migrate_channels(values, version=version)
        result.extend((task_id, channel, value) for channel, value in migrated.items())
    return result
