"""Neo4j GraphStore implementation with parameterized Cypher only (NFR-07)."""

from __future__ import annotations

import re
from typing import Any

from neo4j import GraphDatabase, Driver

from agentic_graphrag.stores.interfaces import EntityRecord, PathRecord, RelationRecord

_SAFE_LABEL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_REL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(value: str, kind: str) -> str:
    pattern = _SAFE_LABEL if kind == "label" else _SAFE_REL
    if not pattern.match(value):
        raise ValueError(f"Invalid {kind} identifier: {value!r}")
    return value


class Neo4jGraphStore:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self._driver.close()

    def clear(self) -> None:
        with self._driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

    def upsert_entities(self, entities: list[EntityRecord]) -> int:
        if not entities:
            return 0
        count = 0
        with self._driver.session() as session:
            for ent in entities:
                label = _validate_identifier(ent.type, "label")
                # Label is validated; properties are parameterized.
                query = (
                    f"MERGE (e:`{label}` {{id: $id}}) "
                    "SET e.name = $name, e.attributes = $attributes, e.aliases = $aliases "
                    "RETURN e.id AS id"
                )
                session.run(
                    query,
                    id=ent.id,
                    name=ent.name,
                    attributes=ent.attributes or {},
                    aliases=ent.aliases or [],
                )
                count += 1
        return count

    def upsert_relations(self, relations: list[RelationRecord]) -> int:
        if not relations:
            return 0
        count = 0
        with self._driver.session() as session:
            for rel in relations:
                rel_type = _validate_identifier(rel.type, "rel")
                query = (
                    "MATCH (h {id: $head_id}), (t {id: $tail_id}) "
                    f"MERGE (h)-[r:`{rel_type}` {{id: $id}}]->(t) "
                    "SET r.confidence = $confidence, r.attributes = $attributes "
                    "RETURN r.id AS id"
                )
                session.run(
                    query,
                    id=rel.id,
                    head_id=rel.head_id,
                    tail_id=rel.tail_id,
                    confidence=rel.confidence,
                    attributes=rel.attributes or {},
                )
                count += 1
        return count

    def get_entity_by_name(
        self, name: str, entity_type: str | None = None
    ) -> EntityRecord | None:
        with self._driver.session() as session:
            if entity_type:
                label = _validate_identifier(entity_type, "label")
                query = f"MATCH (e:`{label}`) WHERE toLower(e.name) = toLower($name) RETURN e LIMIT 1"
            else:
                query = "MATCH (e) WHERE toLower(e.name) = toLower($name) RETURN e LIMIT 1"
            result = session.run(query, name=name)
            record = result.single()
            if not record:
                return None
            node = record["e"]
            return _node_to_entity(node)

    def neighbors(
        self,
        entity_name: str,
        *,
        max_hops: int = 1,
        relation_types: list[str] | None = None,
        limit: int = 50,
    ) -> list[tuple[RelationRecord, EntityRecord]]:
        max_hops = max(1, min(max_hops, 5))
        limit = max(1, min(limit, 200))
        rel_filter = ""
        params: dict[str, Any] = {"name": entity_name, "limit": limit, "max_hops": max_hops}
        if relation_types:
            for rt in relation_types:
                _validate_identifier(rt, "rel")
            rel_filter = "AND type(r) IN $rel_types"
            params["rel_types"] = relation_types

        query = f"""
        MATCH (src)
        WHERE toLower(src.name) = toLower($name)
        MATCH path = (src)-[r*1..{max_hops}]-(dst)
        WHERE src <> dst {rel_filter.replace('r', 'last(relationships(path))') if rel_filter else ''}
        WITH dst, last(relationships(path)) AS r, length(path) AS hops
        RETURN dst, r, hops
        LIMIT $limit
        """
        # Simpler 1-hop optimized path when max_hops == 1
        if max_hops == 1:
            if relation_types:
                query = """
                MATCH (src)-[r]-(dst)
                WHERE toLower(src.name) = toLower($name) AND type(r) IN $rel_types AND src <> dst
                RETURN dst, r, 1 AS hops
                LIMIT $limit
                """
            else:
                query = """
                MATCH (src)-[r]-(dst)
                WHERE toLower(src.name) = toLower($name) AND src <> dst
                RETURN dst, r, 1 AS hops
                LIMIT $limit
                """

        out: list[tuple[RelationRecord, EntityRecord]] = []
        with self._driver.session() as session:
            for record in session.run(query, **params):
                dst = _node_to_entity(record["dst"])
                rel = _rel_to_record(record["r"], head_name=entity_name, tail_name=dst.name)
                out.append((rel, dst))
        return out

    def paths(
        self,
        source_name: str,
        target_name: str,
        *,
        max_hops: int = 4,
        limit: int = 20,
    ) -> list[PathRecord]:
        max_hops = max(1, min(max_hops, 6))
        limit = max(1, min(limit, 50))
        # Variable-length path bound is integer-inlined after validation (not user string).
        query = f"""
        MATCH (src), (dst)
        WHERE toLower(src.name) = toLower($source) AND toLower(dst.name) = toLower($target)
        MATCH path = shortestPath((src)-[*1..{max_hops}]-(dst))
        RETURN path
        LIMIT $limit
        """
        results: list[PathRecord] = []
        with self._driver.session() as session:
            for record in session.run(query, source=source_name, target=target_name, limit=limit):
                path = record["path"]
                nodes = [_node_to_entity(n) for n in path.nodes]
                rels = [
                    _rel_to_record(r, head_name=nodes[i].name, tail_name=nodes[i + 1].name)
                    for i, r in enumerate(path.relationships)
                ]
                results.append(
                    PathRecord(nodes=nodes, relations=rels, length=len(rels), score=1.0 / max(len(rels), 1))
                )
        return results

    def counts(self) -> dict[str, int]:
        with self._driver.session() as session:
            nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rels = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        return {"nodes": int(nodes), "relationships": int(rels)}


def _node_to_entity(node: Any) -> EntityRecord:
    labels = list(node.labels) if hasattr(node, "labels") else []
    etype = labels[0] if labels else "Entity"
    props = dict(node)
    return EntityRecord(
        id=str(props.get("id", props.get("name", ""))),
        name=str(props.get("name", "")),
        type=etype,
        attributes=props.get("attributes") or {},
        aliases=list(props.get("aliases") or []),
    )


def _rel_to_record(rel: Any, head_name: str = "", tail_name: str = "") -> RelationRecord:
    props = dict(rel)
    return RelationRecord(
        id=str(props.get("id", f"{rel.type}:{head_name}->{tail_name}")),
        type=rel.type,
        head_id=str(props.get("head_id", "")),
        tail_id=str(props.get("tail_id", "")),
        head_name=head_name,
        tail_name=tail_name,
        confidence=float(props.get("confidence", 1.0)),
        attributes=props.get("attributes") or {},
    )
