"""Knowledge-bound graph access without unscoped queries or ambiguous name lookup."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from agentic_graphrag.core.ports.capabilities import (
    DEFAULT_RELATION_SCAN,
    EntityLookup,
    EntityMutation,
    EntityNameLookup,
    EntityNames,
    ProcessLocalIndex,
    RelationDeletion,
)
from agentic_graphrag.core.ports.stores import EntityRecord, GraphStore, PathRecord, RelationRecord
from agentic_graphrag.stores.scope_codec import ScopeBinding
from agentic_graphrag.stores.scoped_graph_scan import entities_in_scope, relations_in_scope


class ScopedGraphStore:
    def __init__(self, store: GraphStore, binding: ScopeBinding) -> None:
        self.store = store
        self.binding = binding

    @property
    def index_generation(self) -> str:
        origin = (
            self.store.index_generation if isinstance(self.store, ProcessLocalIndex) else "shared"
        )
        return f"{self.binding.owner}:{origin}"

    def clear(self) -> None:
        raise ValueError("Unscoped clear is unavailable on a bound knowledge store")

    def close(self) -> None:
        """The owning unbound bundle controls shared client lifetimes."""

    def upsert_entities(self, entities: list[EntityRecord]) -> int:
        self.binding.check(write=True)
        return self.store.upsert_entities([self.binding.encode(item) for item in entities])

    def upsert_relations(self, relations: list[RelationRecord]) -> int:
        self.binding.check(write=True)
        return self.store.upsert_relations([self.binding.encode(item) for item in relations])

    def delete_by_doc(self, doc_id: str, tenant_id: str) -> int:
        return self.store.delete_by_doc(doc_id, self.binding.check(tenant_id, write=True))

    def list_entities(
        self, *, limit: int = 50, offset: int = 0, tenant_id: str | None = None
    ) -> list[EntityRecord]:
        rows = self.store.list_entities(
            limit=limit, offset=offset, tenant_id=self.binding.check(tenant_id)
        )
        return [self.binding.decode(row) for row in rows]

    def list_relations(self, limit: int = DEFAULT_RELATION_SCAN) -> list[RelationRecord]:
        from itertools import islice

        rows = islice(relations_in_scope(self.store, self.binding.owner), max(0, limit))
        return [self.binding.decode(row) for row in rows]

    def scan_relations(
        self, *, tenant_id: str, limit: int = 1000, offset: int = 0
    ) -> list[RelationRecord]:
        from itertools import islice

        owner = self.binding.check(tenant_id)
        rows = islice(
            relations_in_scope(self.store, owner), max(0, offset), max(0, offset) + max(0, limit)
        )
        return [self.binding.decode(row) for row in rows]

    def counts(self) -> dict[str, int]:
        nodes = sum(1 for _ in entities_in_scope(self.store, self.binding.owner))
        relations = sum(1 for _ in relations_in_scope(self.store, self.binding.owner))
        return {
            "nodes": nodes,
            "entities": nodes,
            "entity_count": nodes,
            "relationships": relations,
            "relations": relations,
        }

    def entities_named(self, name: str, *, tenant_id: str | None = None) -> list[EntityRecord]:
        return [self.binding.decode(row) for row in self._named_rows(name, tenant_id=tenant_id)]

    def _named_rows(self, name: str, *, tenant_id: str | None) -> list[EntityRecord]:
        owner = self.binding.check(tenant_id)
        if isinstance(self.store, EntityNameLookup):
            rows = self.store.entities_named(name, tenant_id=owner)
        else:
            rows = [
                row
                for row in entities_in_scope(self.store, owner)
                if row.name.casefold() == name.casefold()
                or name.casefold() in {alias.casefold() for alias in row.aliases}
            ]
        if any((row.tenant_id or "default") != owner for row in rows):
            raise ValueError("Storage returned an entity outside the bound knowledge scope")
        return rows

    def entity_names(self, *, tenant_id: str) -> list[str]:
        owner = self.binding.check(tenant_id)
        if isinstance(self.store, EntityNames):
            return self.store.entity_names(tenant_id=owner)
        return list(dict.fromkeys(row.name for row in entities_in_scope(self.store, owner)))

    def get_entity_by_name(
        self, name: str, entity_type: str | None = None, *, tenant_id: str | None = None
    ) -> EntityRecord | None:
        rows = [
            row
            for row in self.entities_named(name, tenant_id=tenant_id)
            if entity_type is None or row.type == entity_type
        ]
        return rows[0] if len(rows) == 1 else None

    def get_entity_by_id(self, entity_id: str, *, tenant_id: str) -> EntityRecord | None:
        owner = self.binding.check(tenant_id)
        if isinstance(self.store, EntityLookup):
            row = self.store.get_entity_by_id(entity_id, tenant_id=owner)
        else:
            row = next((e for e in entities_in_scope(self.store, owner) if e.id == entity_id), None)
        return self.binding.decode(row) if row is not None else None

    def neighbors(
        self,
        entity_name: str,
        *,
        max_hops: int = 1,
        relation_types: list[str] | None = None,
        limit: int = 50,
        tenant_id: str | None = None,
    ) -> list[tuple[RelationRecord, EntityRecord]]:
        owner = self.binding.check(tenant_id)
        matches = self._named_rows(entity_name, tenant_id=tenant_id)
        if len(matches) != 1:
            return []
        rows = self.store.neighbors(
            matches[0].name,
            max_hops=max_hops,
            relation_types=relation_types,
            limit=limit,
            tenant_id=owner,
        )
        memo: dict[int, Any] = {}
        return [
            (self.binding.decode(edge, memo=memo), self.binding.decode(entity, memo=memo))
            for edge, entity in rows
        ]

    def neighbors_by_id(
        self,
        entity_id: str,
        *,
        tenant_id: str,
        limit: int = 50,
        relation_types: list[str] | None = None,
    ) -> list[tuple[RelationRecord, EntityRecord]]:
        owner = self.binding.check(tenant_id)
        rows: list[tuple[RelationRecord, EntityRecord]] = []
        for edge in relations_in_scope(self.store, owner):
            if relation_types and edge.type not in relation_types:
                continue
            other = edge.tail_id if edge.head_id == entity_id else edge.head_id
            if entity_id not in {edge.head_id, edge.tail_id}:
                continue
            entity = self.get_entity_by_id(other, tenant_id=tenant_id)
            if entity is not None:
                rows.append((self.binding.decode(edge), entity))
            if len(rows) >= limit:
                break
        return rows

    def paths(
        self,
        source_name: str,
        target_name: str,
        *,
        max_hops: int = 4,
        limit: int = 20,
        tenant_id: str | None = None,
    ) -> list[PathRecord]:
        owner = self.binding.check(tenant_id)
        source = self._named_rows(source_name, tenant_id=tenant_id)
        target = self._named_rows(target_name, tenant_id=tenant_id)
        if len(source) != 1 or len(target) != 1:
            return []
        paths = self.store.paths(
            source[0].name,
            target[0].name,
            max_hops=max_hops,
            limit=limit,
            tenant_id=owner,
        )
        memo: dict[int, Any] = {}
        return [
            replace(
                path,
                nodes=[self.binding.decode(n, memo=memo) for n in path.nodes],
                relations=[self.binding.decode(r, memo=memo) for r in path.relations],
            )
            for path in paths
        ]

    def delete_relation(self, relation_id: str, *, tenant_id: str | None = None) -> bool:
        owner = self.binding.check(tenant_id, write=True)
        if not isinstance(self.store, RelationDeletion):
            raise TypeError("Graph backend does not support relation deletion")
        return self.store.delete_relation(relation_id, tenant_id=owner)

    def rewire_entity(self, drop_id: str, keep_id: str, *, keep_name: str, tenant_id: str) -> None:
        owner = self.binding.check(tenant_id, write=True)
        if not isinstance(self.store, EntityMutation):
            raise TypeError("Graph backend does not support entity rewiring")
        self.store.rewire_entity(drop_id, keep_id, keep_name=keep_name, tenant_id=owner)

    def delete_entity(self, entity_id: str, *, tenant_id: str) -> None:
        owner = self.binding.check(tenant_id, write=True)
        if not isinstance(self.store, EntityMutation):
            raise TypeError("Graph backend does not support entity deletion")
        self.store.delete_entity(entity_id, tenant_id=owner)
