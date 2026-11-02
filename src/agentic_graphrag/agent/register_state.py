"""Typed register bookkeeping and append-only progress history."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, NotRequired, TypedDict

from agentic_graphrag.agent.requirements import Register, RequirementChange


class InitializationAudit(TypedDict, total=False):
    evidence_summary: str
    reasoning_note: str


class VerificationEvent(TypedDict, total=False):
    type: Literal["resolution_proposal"]
    req_id: str
    passages: list[str]
    entailed: bool
    reason: str
    error: str
    accepted: bool


class InitializationEvent(TypedDict):
    type: Literal["initialization"]
    round: int
    error: str
    register: Register
    seed_ids: list[str]
    evidence_summary: NotRequired[str]
    reasoning_note: NotRequired[str]


class ProgressEvent(TypedDict):
    type: Literal["progress"]
    round: int
    changes: list[RequirementChange]
    verifier: list[VerificationEvent]
    new_passages: list[str]


type RegisterEvent = InitializationEvent | ProgressEvent


@dataclass
class RegisterRuntimeState:
    """The auxiliary register_* channels, alongside AgentState.register."""

    context: list[dict[str, str]] = field(default_factory=list)
    events: list[RegisterEvent] = field(default_factory=list)
    cache: dict[str, list[str]] = field(default_factory=dict)
    seen: list[str] = field(default_factory=list)
    attempts: dict[str, int] = field(default_factory=dict)
    fresh: list[str] = field(default_factory=list)
    stop: str = ""
    progress: str = ""
    answer_policy: str = ""


def append_events(
    previous: list[RegisterEvent], incoming: list[RegisterEvent]
) -> list[RegisterEvent]:
    """Merge full node snapshots or deltas without replaying a round's event."""
    events = list(previous)
    seen = {(event["type"], event["round"]) for event in events}
    for event in incoming:
        key = (event["type"], event["round"])
        if key not in seen:
            events.append(event)
            seen.add(key)
    return events


def merge_register_runtime(
    previous: RegisterRuntimeState, incoming: RegisterRuntimeState
) -> RegisterRuntimeState:
    return replace(incoming, events=append_events(previous.events, incoming.events))
