"""Optional backend capabilities with compiler-checked method signatures."""

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentic_graphrag.core.constants import DEFAULT_PAGE_SIZE
from agentic_graphrag.core.ports.stores import EntityRecord, RelationRecord

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
