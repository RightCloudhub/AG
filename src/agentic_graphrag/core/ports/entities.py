"""Entity semantic retrieval and canonical mention mappings, separate from chunks."""

from __future__ import annotations

import math
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentic_graphrag.core.knowledge import KnowledgeScope, SourceRef


class SemanticEntity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: KnowledgeScope
    entity_id: str
    name: str
    entity_type: str
    description: str
    aliases: tuple[str, ...] = ()
    vector: tuple[float, ...]
    sources: tuple[SourceRef, ...] = ()

    @model_validator(mode="after")
    def valid_vector(self) -> Self:
        if not self.vector or not all(math.isfinite(v) for v in self.vector):
            raise ValueError("Entity vector must contain finite values")
        if any(source.scope != self.scope for source in self.sources):
            raise ValueError("Entity sources must belong to the same knowledge scope")
        return self


class EntityMatch(BaseModel):
    entity: SemanticEntity
    score: float = Field(ge=-1, le=1)


@runtime_checkable
class EntityEmbedder(Protocol):
    def embed_many(self, texts: list[str]) -> list[list[float]]: ...


class EntitySemanticIndex(Protocol):
    """The embedding space includes provider/model revision, dimension and normalization."""

    def upsert(self, entities: list[SemanticEntity], *, space: str) -> None: ...

    def copy_identity(
        self,
        entities: list[SemanticEntity],
        *,
        scope: KnowledgeScope,
        source_space: str,
        target_space: str,
    ) -> None: ...

    def get(
        self, entity_id: str, *, scope: KnowledgeScope, space: str
    ) -> SemanticEntity | None: ...

    def search(
        self, vector: list[float], *, scope: KnowledgeScope, space: str, top_k: int = 12
    ) -> list[EntityMatch]: ...

    def aliases(
        self, alias: str, *, scope: KnowledgeScope, space: str, top_k: int = 12
    ) -> list[SemanticEntity]: ...

    def confirm_alias(
        self, alias: str, entity_id: str, *, scope: KnowledgeScope, space: str
    ) -> None: ...

    def confirmed_entity(self, alias: str, *, scope: KnowledgeScope, space: str) -> str | None: ...

    def mention(self, mention_id: str, *, scope: KnowledgeScope, space: str) -> str | None: ...

    def bind_mention(
        self, mention_id: str, entity_id: str, *, source: SourceRef, space: str
    ) -> None: ...

    def redirect(
        self, old_id: str, new_id: str, *, scope: KnowledgeScope, space: str, operation_id: str
    ) -> None: ...

    def remove_redirect(
        self, old_id: str, *, scope: KnowledgeScope, space: str, operation_id: str
    ) -> None: ...

    def close(self) -> None: ...
