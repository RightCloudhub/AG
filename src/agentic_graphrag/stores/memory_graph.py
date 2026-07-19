"""In-memory GraphStore for offline POC / tests (no Neo4j required)."""

from __future__ import annotations

from agentic_graphrag.stores.interfaces import EntityRecord, PathRecord, RelationRecord


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
        # BFS up to max_hops. Record every edge seen (even into already-visited
        # nodes) so multi-hop evidence keeps WORKED_AT / CEO edges of intermediate people.
        start = entity_name.lower()
        visited_nodes = {start}
        frontier = {start}
        results: list[tuple[RelationRecord, EntityRecord]] = []
        seen_edges: set[str] = set()

        for _ in range(max(1, max_hops)):
            next_frontier: set[str] = set()
            for node in frontier:
                for rel, other in self._edges_of(node, relation_types):
                    edge_key = rel.id or f"{rel.type}:{rel.head_name}:{rel.tail_name}:{other.name}"
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        results.append((rel, other))
                        if len(results) >= limit:
                            return results
                    key = other.name.lower()
                    if key not in visited_nodes:
                        visited_nodes.add(key)
                        next_frontier.add(key)
            frontier = next_frontier
            if not frontier:
                break
        return results

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

        # BFS paths
        queue: list[tuple[str, list[EntityRecord], list[RelationRecord]]] = [
            (src, [self._by_name[src]], [])
        ]
        found: list[PathRecord] = []
        while queue and len(found) < limit:
            node, nodes, rels = queue.pop(0)
            if len(rels) >= max_hops:
                continue
            for rel, other in self._edges_of(node, None):
                if other.name.lower() in {n.name.lower() for n in nodes}:
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
        return found

    def counts(self) -> dict[str, int]:
        return {"nodes": len(self._entities), "relationships": len(self._relations)}

    def close(self) -> None:
        return None

    def _edges_of(
        self, name_lower: str, relation_types: list[str] | None
    ) -> list[tuple[RelationRecord, EntityRecord]]:
        out: list[tuple[RelationRecord, EntityRecord]] = []
        for r in self._relations:
            if relation_types and r.type not in relation_types:
                continue
            if r.head_name.lower() == name_lower:
                other = self._entities.get(r.tail_id) or EntityRecord(
                    id=r.tail_id, name=r.tail_name, type="Entity"
                )
                out.append((r, other))
            elif r.tail_name.lower() == name_lower:
                other = self._entities.get(r.head_id) or EntityRecord(
                    id=r.head_id, name=r.head_name, type="Entity"
                )
                out.append((r, other))
        return out
