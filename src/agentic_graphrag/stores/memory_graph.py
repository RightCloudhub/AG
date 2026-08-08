"""In-memory GraphStore for offline POC / tests (no Neo4j required)."""

from __future__ import annotations

from collections import deque

from agentic_graphrag.stores.interfaces import EntityRecord, PathRecord, RelationRecord


class InMemoryGraphStore:
    def __init__(self) -> None:
        self._entities: dict[str, EntityRecord] = {}
        self._relations: dict[str, RelationRecord] = {}

    def clear(self) -> None:
        self._entities.clear()
        self._relations.clear()

    def upsert_entities(self, entities: list[EntityRecord]) -> int:
        for entity in entities:
            self._entities[_key(entity.tenant_id, entity.id)] = entity
        return len(entities)

    def upsert_relations(self, relations: list[RelationRecord]) -> int:
        for relation in relations:
            self._relations[_key(relation.tenant_id, relation.id)] = relation
        return len(relations)

    def delete_relation(self, relation_id: str) -> bool:
        """Remove an edge by id across tenants; mirrors ``Neo4jGraphStore``.

        Needed by the incremental updater: without it an auto-merged conflict
        wrote the new edge while the superseded one stayed, leaving two
        contradictory facts on the offline path (docs/BUSINESS_LOGIC.md BL-10).
        """
        keys = [k for k, rec in self._relations.items() if rec.id == relation_id]
        for key in keys:
            del self._relations[key]
        return bool(keys)

    def get_entity_by_name(self, name: str, entity_type: str | None = None) -> EntityRecord | None:
        target = name.lower()
        for entity in self._entities.values():
            if entity.name.lower() != target:
                continue
            if entity_type is None or entity.type == entity_type:
                return entity
        return None

    def neighbors(
        self,
        entity_name: str,
        *,
        max_hops: int = 1,
        relation_types: list[str] | None = None,
        limit: int = 50,
        tenant_id: str | None = None,
    ) -> list[tuple[RelationRecord, EntityRecord]]:
        frontier = {entity_name.lower()}
        visited = set(frontier)
        seen_edges: set[str] = set()
        results: list[tuple[RelationRecord, EntityRecord]] = []
        for _ in range(max(1, max_hops)):
            frontier = self._expand_neighbors(
                frontier,
                visited=visited,
                seen_edges=seen_edges,
                results=results,
                relation_types=relation_types,
                tenant_id=tenant_id,
                limit=limit,
            )
            if not frontier or len(results) >= limit:
                break
        return results[:limit]

    def _expand_neighbors(
        self,
        frontier: set[str],
        *,
        visited: set[str],
        seen_edges: set[str],
        results: list[tuple[RelationRecord, EntityRecord]],
        relation_types: list[str] | None,
        tenant_id: str | None,
        limit: int,
    ) -> set[str]:
        next_frontier: set[str] = set()
        for node in sorted(frontier):
            for relation, other in self._edges_of(node, relation_types, tenant_id):
                edge_key = _key(relation.tenant_id, relation.id)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    results.append((relation, other))
                other_name = other.name.lower()
                if other_name not in visited:
                    visited.add(other_name)
                    next_frontier.add(other_name)
                if len(results) >= limit:
                    return next_frontier
        return next_frontier

    def paths(
        self,
        source_name: str,
        target_name: str,
        *,
        max_hops: int = 4,
        limit: int = 20,
        tenant_id: str | None = None,
    ) -> list[PathRecord]:
        source = self._entity_for_name(source_name, tenant_id)
        target = self._entity_for_name(target_name, tenant_id)
        if source is None or target is None:
            return []
        queue = deque([(source, [source], [])])
        found: list[PathRecord] = []
        while queue and len(found) < limit:
            current, nodes, relations = queue.popleft()
            if len(relations) >= max_hops:
                continue
            self._extend_paths(
                queue,
                found,
                current=current,
                nodes=nodes,
                relations=relations,
                target=target,
                tenant_id=tenant_id,
            )
        return found

    def _extend_paths(
        self,
        queue: deque,
        found: list[PathRecord],
        *,
        current: EntityRecord,
        nodes: list[EntityRecord],
        relations: list[RelationRecord],
        target: EntityRecord,
        tenant_id: str | None,
    ) -> None:
        seen = {node.name.lower() for node in nodes}
        for relation, other in self._edges_of(current.name.lower(), None, tenant_id):
            if other.name.lower() in seen:
                continue
            new_nodes = [*nodes, other]
            new_relations = [*relations, relation]
            if other.id == target.id and _tenant_matches(other.tenant_id, tenant_id):
                found.append(_path(new_nodes, new_relations))
            else:
                queue.append((other, new_nodes, new_relations))

    def counts(self) -> dict[str, int]:
        entities = len(self._entities)
        relations = len(self._relations)
        return {
            "nodes": entities,
            "entities": entities,
            "entity_count": entities,
            "relationships": relations,
            "relations": relations,
        }

    def list_entities(
        self, *, limit: int = 50, offset: int = 0, tenant_id: str | None = None
    ) -> list[EntityRecord]:
        items = [
            entity
            for entity in self._entities.values()
            if _tenant_matches(entity.tenant_id, tenant_id)
        ]
        items.sort(key=lambda entity: (entity.type, entity.name.lower()))
        return items[max(0, offset) : max(0, offset) + max(0, limit)]

    def close(self) -> None:
        return None

    def _edges_of(
        self,
        name: str,
        relation_types: list[str] | None,
        tenant_id: str | None,
    ) -> list[tuple[RelationRecord, EntityRecord]]:
        rows: list[tuple[RelationRecord, EntityRecord]] = []
        for relation in self._relations.values():
            if not _tenant_matches(relation.tenant_id, tenant_id):
                continue
            endpoint = self._endpoint(relation, name, relation_types)
            if endpoint is not None:
                rows.append((relation, endpoint))
        return rows

    def _endpoint(
        self,
        relation: RelationRecord,
        name: str,
        relation_types: list[str] | None,
    ) -> EntityRecord | None:
        if relation_types and relation.type not in relation_types:
            return None
        if relation.head_name.lower() == name:
            return self._entity_or_stub(relation.tail_id, relation.tail_name, relation.tenant_id)
        if relation.tail_name.lower() == name:
            return self._entity_or_stub(relation.head_id, relation.head_name, relation.tenant_id)
        return None

    def _entity_or_stub(self, entity_id: str, name: str, tenant_id: str) -> EntityRecord:
        return self._entities.get(_key(tenant_id, entity_id)) or EntityRecord(
            id=entity_id, name=name, type="Entity", tenant_id=tenant_id
        )

    def _entity_for_name(self, name: str, tenant_id: str | None) -> EntityRecord | None:
        target = name.lower()
        return next(
            (
                entity
                for entity in self._entities.values()
                if entity.name.lower() == target and _tenant_matches(entity.tenant_id, tenant_id)
            ),
            None,
        )


def _path(nodes: list[EntityRecord], relations: list[RelationRecord]) -> PathRecord:
    return PathRecord(
        nodes=nodes,
        relations=relations,
        length=len(relations),
        score=1.0 / max(len(relations), 1),
    )


def _key(tenant_id: str, value: str) -> str:
    return f"{tenant_id}\0{value}"


def _tenant_matches(owner: str, requested: str | None) -> bool:
    """Allow legacy seed records as shared; isolate explicitly tagged records."""
    if requested is None:
        return True
    if owner == "":
        return True
    if requested == "default":
        return owner == "default"
    return owner == requested
