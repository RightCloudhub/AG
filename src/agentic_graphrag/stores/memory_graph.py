"""In-memory GraphStore for offline POC / tests (no Neo4j required)."""

from __future__ import annotations

from dataclasses import dataclass, field

from agentic_graphrag.stores.interfaces import EntityRecord, PathRecord, RelationRecord


@dataclass
class _NeighborState:
    visited_nodes: set[str]
    frontier: set[str]
    results: list[tuple[RelationRecord, EntityRecord]] = field(default_factory=list)
    seen_edges: set[str] = field(default_factory=set)


class InMemoryGraphStore:
    def __init__(self) -> None:
        self._entities: dict[str, EntityRecord] = {}
        self._by_name: dict[str, EntityRecord] = {}
        self._relations: list[RelationRecord] = []

    def clear(self) -> None:
        self._entities.clear()
        self._by_name.clear()
        self._relations.clear()

    def upsert_entities(self, entities: list[EntityRecord]) -> int:
        for e in entities:
            self._entities[e.id] = e
            self._by_name[e.name.lower()] = e
        return len(entities)

    def upsert_relations(self, relations: list[RelationRecord]) -> int:
        by_id = {r.id: r for r in self._relations}
        for r in relations:
            by_id[r.id] = r
        self._relations = list(by_id.values())
        return len(relations)

    def get_entity_by_name(self, name: str, entity_type: str | None = None) -> EntityRecord | None:
        ent = self._by_name.get(name.lower())
        if ent and entity_type and ent.type != entity_type:
            return None
        return ent

    def neighbors(
        self,
        entity_name: str,
        *,
        max_hops: int = 1,
        relation_types: list[str] | None = None,
        limit: int = 50,
    ) -> list[tuple[RelationRecord, EntityRecord]]:
        start = entity_name.lower()
        state = _NeighborState(visited_nodes={start}, frontier={start})
        for _ in range(max(1, max_hops)):
            if self._expand_frontier(state, relation_types, limit):
                return state.results
            if not state.frontier:
                break
        return state.results

    def _expand_frontier(
        self,
        state: _NeighborState,
        relation_types: list[str] | None,
        limit: int,
    ) -> bool:
        """Return True when limit is hit."""
        next_frontier: set[str] = set()
        for node in state.frontier:
            if self._expand_node(
                node,
                state=state,
                relation_types=relation_types,
                next_frontier=next_frontier,
                limit=limit,
            ):
                return True
        state.frontier = next_frontier
        return False

    def _expand_node(
        self,
        node: str,
        *,
        state: _NeighborState,
        relation_types: list[str] | None,
        next_frontier: set[str],
        limit: int,
    ) -> bool:
        for rel, other in self._edges_of(node, relation_types):
            if self._maybe_record(rel, other, state) and len(state.results) >= limit:
                return True
            key = other.name.lower()
            if key not in state.visited_nodes:
                state.visited_nodes.add(key)
                next_frontier.add(key)
        return False

    def _maybe_record(
        self,
        rel: RelationRecord,
        other: EntityRecord,
        state: _NeighborState,
    ) -> bool:
        edge_key = rel.id or f"{rel.type}:{rel.head_name}:{rel.tail_name}:{other.name}"
        if edge_key in state.seen_edges:
            return False
        state.seen_edges.add(edge_key)
        state.results.append((rel, other))
        return True

    def paths(
        self,
        source_name: str,
        target_name: str,
        *,
        max_hops: int = 4,
        limit: int = 20,
    ) -> list[PathRecord]:
        src = source_name.lower()
        dst = target_name.lower()
        if src not in self._by_name or dst not in self._by_name:
            return []
        queue: list[tuple[str, list[EntityRecord], list[RelationRecord]]] = [
            (src, [self._by_name[src]], [])
        ]
        found: list[PathRecord] = []
        while queue and len(found) < limit:
            node, nodes, rels = queue.pop(0)
            if len(rels) >= max_hops:
                continue
            self._expand_path(node, nodes, rels, dst=dst, queue=queue, found=found)
        return found

    def _expand_path(
        self,
        node: str,
        nodes: list[EntityRecord],
        rels: list[RelationRecord],
        *,
        dst: str,
        queue: list[tuple[str, list[EntityRecord], list[RelationRecord]]],
        found: list[PathRecord],
    ) -> None:
        seen_names = {n.name.lower() for n in nodes}
        for rel, other in self._edges_of(node, None):
            if other.name.lower() in seen_names:
                continue
            new_nodes = nodes + [other]
            new_rels = rels + [rel]
            if other.name.lower() == dst:
                found.append(
                    PathRecord(
                        nodes=new_nodes,
                        relations=new_rels,
                        length=len(new_rels),
                        score=1.0 / max(len(new_rels), 1),
                    )
                )
            else:
                queue.append((other.name.lower(), new_nodes, new_rels))

    def counts(self) -> dict[str, int]:
        n = len(self._entities)
        r = len(self._relations)
        return {
            "nodes": n,
            "entities": n,
            "entity_count": n,
            "relationships": r,
            "relations": r,
        }

    def list_entities(self, *, limit: int = 50, offset: int = 0) -> list[EntityRecord]:
        items = list(self._entities.values())
        items.sort(key=lambda e: (e.type, e.name.lower()))
        return items[max(0, offset) : max(0, offset) + max(0, limit)]

    def close(self) -> None:
        return None

    def _edges_of(
        self, name_lower: str, relation_types: list[str] | None
    ) -> list[tuple[RelationRecord, EntityRecord]]:
        out: list[tuple[RelationRecord, EntityRecord]] = []
        for r in self._relations:
            pair = self._edge_endpoint(r, name_lower, relation_types)
            if pair is not None:
                out.append(pair)
        return out

    def _edge_endpoint(
        self,
        r: RelationRecord,
        name_lower: str,
        relation_types: list[str] | None,
    ) -> tuple[RelationRecord, EntityRecord] | None:
        if relation_types and r.type not in relation_types:
            return None
        if r.head_name.lower() == name_lower:
            other = self._entities.get(r.tail_id) or EntityRecord(
                id=r.tail_id, name=r.tail_name, type="Entity"
            )
            return (r, other)
        if r.tail_name.lower() == name_lower:
            other = self._entities.get(r.head_id) or EntityRecord(
                id=r.head_id, name=r.head_name, type="Entity"
            )
            return (r, other)
        return None
