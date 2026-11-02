"""Optional backend capabilities with compiler-checked method signatures."""

from abc import abstractmethod
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from agentic_graphrag.core.constants import DEFAULT_PAGE_SIZE
from agentic_graphrag.core.ports.stores import ChunkRecord, EntityRecord, RelationRecord

DEFAULT_RELATION_SCAN = 50_000


@runtime_checkable
class Closable(Protocol):
    @abstractmethod
    def close(self) -> None: ...


@runtime_checkable
class Pingable(Protocol):
    @abstractmethod
    def ping(self) -> None: ...


@runtime_checkable
class SnapshotLoader(Protocol):
    @abstractmethod
    def load(self, path: str) -> int: ...


@runtime_checkable
class EntityLister(Protocol):
    @abstractmethod
    def list_entities(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0, tenant_id: str | None = None
    ) -> list[EntityRecord]: ...


@runtime_checkable
class RelationLister(Protocol):
    @abstractmethod
    def list_relations(self, limit: int = DEFAULT_RELATION_SCAN) -> list[RelationRecord]: ...


@runtime_checkable
class RelationDeletion(Protocol):
    @abstractmethod
    def delete_relation(self, relation_id: str, *, tenant_id: str | None = None) -> bool: ...


@runtime_checkable
class EntityMutation(Protocol):
    @abstractmethod
    def rewire_entity(
        self, drop_id: str, keep_id: str, *, keep_name: str, tenant_id: str
    ) -> None: ...

    @abstractmethod
    def delete_entity(self, entity_id: str, *, tenant_id: str) -> None: ...


@runtime_checkable
class VectorModelRebuild(Protocol):
    """Stage a tenant's new vector space and atomically publish it."""

    def begin_reindex(self, model: str, *, tenant_id: str) -> None: ...

    def commit_reindex(self) -> None: ...

    def end_reindex(self) -> None: ...


@runtime_checkable
class ModelVectorSearch(Protocol):
    def search_with_model(
        self,
        query: str,
        embed: Callable[[str, str], list[float]],
        *,
        top_k: int,
        tenant_id: str,
    ) -> list[tuple[ChunkRecord, float]]: ...
