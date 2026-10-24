"""Auditable information needs, separate from the executor's task DAG."""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from agentic_graphrag.core.enums import RequirementStatus
from agentic_graphrag.core.json_types import JsonObject

Status = RequirementStatus
OPEN_STATUSES = frozenset({RequirementStatus.PENDING, RequirementStatus.ACTIVE})


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    req_id: str = ""
    text: str = Field(min_length=1)
    status: RequirementStatus = RequirementStatus.PENDING
    notes: str = Field(default="", validation_alias=AliasChoices("notes", "evidence_notes"))
    passages: set[str] = Field(default_factory=set)
    retire_reason: str = ""
    anchors: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    # An update must name the fresh passages that actually touched this need.
    touched_passages: set[str] = Field(default_factory=set)

    def snapshot(self) -> JsonObject:
        data = self.model_dump(mode="json")
        data["passages"] = sorted(self.passages)
        data["touched_passages"] = sorted(self.touched_passages)
        return data


class Register(BaseModel):
    question: str
    items: dict[str, Requirement] = Field(default_factory=dict)

    def open_items(self) -> list[Requirement]:
        return [r for r in self.items.values() if r.status in OPEN_STATUSES]

    def is_clear(self) -> bool:
        return not self.open_items()

    def ready_items(self) -> list[Requirement]:
        return [r for r in self.open_items() if self.dependencies_met(r)]

    def dependencies_met(self, item: Requirement) -> bool:
        return all(self.items[d].status == RequirementStatus.RESOLVED for d in item.depends_on)

    def search_target(self, item: Requirement) -> Requirement:
        anchors = list(item.anchors)
        for rid in item.depends_on:
            anchors.extend(self.items[rid].anchors)
        return item.model_copy(update={"anchors": list(dict.fromkeys(anchors))})

    def snapshot(self) -> JsonObject:
        return {
            "question": self.question,
            "items": {k: r.snapshot() for k, r in self.items.items()},
        }


class RegisterPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_summary: str = ""
    requirement_register: dict[str, Requirement]
    reasoning_note: str = ""


def compose(question: str, item: Requirement) -> str:
    """Keep the global goal, the current gap and confirmed bridge entities."""
    parts = [question, item.text, item.notes, " ".join(item.anchors)]
    return "\n".join(p for p in parts if p)


def diff(old: Register, new: Register) -> list[JsonObject]:
    changes = []
    for rid, item in new.items.items():
        previous = old.items.get(rid)
        if previous is None or previous.snapshot() != item.snapshot():
            changes.append(
                {
                    "req_id": rid,
                    "before": previous.status if previous else "new",
                    "after": item.status,
                    "requirement": item.snapshot(),
                }
            )
    return changes
