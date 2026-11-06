"""Strict model output for open extraction; IDs and source versions are server-owned."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

OpenName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]
MAX_ENTITIES_PER_CHUNK = 128
MAX_RELATIONS_PER_CHUNK = 256
MAX_ALIASES_PER_ENTITY = 32


class OpenSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote: str = Field(min_length=1, max_length=8000)
    start: int | None = Field(default=None, ge=0)


class OpenEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: OpenName
    name: OpenName
    entity_type: OpenName = "entity"
    description: Description
    aliases: list[OpenName] = Field(default_factory=list, max_length=MAX_ALIASES_PER_ENTITY)
    evidence: OpenSpan


class OpenRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    head: OpenName
    tail: OpenName
    predicate: OpenName
    description: Description
    confidence: float = Field(default=1.0, ge=0, le=1)
    evidence: OpenSpan


class OpenExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: list[OpenEntity] = Field(default_factory=list, max_length=MAX_ENTITIES_PER_CHUNK)
    relations: list[OpenRelation] = Field(default_factory=list, max_length=MAX_RELATIONS_PER_CHUNK)

    @model_validator(mode="after")
    def endpoints_exist(self) -> Self:
        refs = {entity.ref for entity in self.entities}
        if len(refs) != len(self.entities):
            raise ValueError("Entity references must be unique within a chunk")
        if any(rel.head not in refs or rel.tail not in refs for rel in self.relations):
            raise ValueError("Relation endpoint does not refer to an extracted entity")
        return self


class ResolutionVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["merge", "separate", "review"]
    candidate_id: str | None = None
    reason: str = Field(min_length=1, max_length=1000)
