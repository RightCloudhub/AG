"""Neo4j GraphStore implementation with parameterized Cypher only (NFR-07).

Relation properties persist semantic endpoints (``head_id`` / ``tail_id`` /
``head_name`` / ``tail_name``) and ``sources`` (JSON string) so undirected
reads can rebuild true direction. Multi-hop neighbors use stable
``ORDER BY hops, r.id`` + edge de-dup; paths use bounded simple-path
enumeration (not ``shortestPath`` alone).

**Migration:** graphs written before these relation properties must be
rebuilt (``agr-build-graph``); old edges lack the new attributes.
"""

from __future__ import annotations

import re
from typing import Any

from neo4j import Driver, GraphDatabase

from agentic_graphrag.stores.interfaces import EntityRecord, PathRecord, RelationRecord
from agentic_graphrag.stores.neo4j_codec import (
    attrs_for_neo4j,
    attrs_from_neo4j,
    node_to_entity,
    rel_to_record,
    sources_for_neo4j,
    sources_from_neo4j,
)

# Back-compat aliases used by unit tests.
_attrs_for_neo4j = attrs_for_neo4j
_attrs_from_neo4j = attrs_from_neo4j
_sources_for_neo4j = sources_for_neo4j
_sources_from_neo4j = sources_from_neo4j
_node_to_entity = node_to_entity
_rel_to_record = rel_to_record

_SAFE_LABEL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_REL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_NEIGHBOR_HOPS = 5
_MAX_NEIGHBOR_LIMIT = 200
_MAX_PATH_HOPS = 6
_MAX_PATH_LIMIT = 50


def _validate_identifier(value: str, kind: str) -> str:
    pattern = _SAFE_LABEL if kind == "label" else _SAFE_REL
    if not pattern.match(value):
        raise ValueError(f"Invalid {kind} identifier: {value!r}")
    return value


class Neo4jGraphStore:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self._uri = uri
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self._driver.close()

    def ping(self) -> None:
        """Fail fast if Neo4j is unreachable (driver connect is lazy)."""
        self._driver.verify_connectivity()

    def clear(self) -> None:
        with self._driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

    def upsert_entities(self, entities: list[EntityRecord]) -> int:
        if not entities:
            return 0
        count = 0
        with self._driver.session() as session:
            for ent in entities:
                if self._upsert_one_entity(session, ent):
                    count += 1
        return count

    def _upsert_one_entity(self, session: Any, ent: EntityRecord) -> bool:
        label = _validate_identifier(ent.type, "label")
        query = (
            f"MERGE (e:`{label}` {{id: $id}}) "
            "SET e.name = $name, e.attributes = $attributes, e.aliases = $aliases "
            "RETURN e.id AS id"
        )
        result = session.run(
            query,
            id=ent.id,
            name=ent.name,
            attributes=attrs_for_neo4j(ent.attributes),
            aliases=list(ent.aliases or []),
        )
        return result.single() is not None

    def upsert_relations(self, relations: list[RelationRecord]) -> int:
        """Upsert relations; return count of relationships actually merged."""
        if not relations:
            return 0
        count = 0
        with self._driver.session() as session:
            for rel in relations:
                if self._upsert_one_relation(session, rel):
                    count += 1
        return count

    def _upsert_one_relation(self, session: Any, rel: RelationRecord) -> bool:
        rel_type = _validate_identifier(rel.type, "rel")
        query = (
            "MERGE (h {id: $head_id}) "
            "ON CREATE SET h.name = $head_name "
            "SET h.name = coalesce(h.name, $head_name) "
            "MERGE (t {id: $tail_id}) "
            "ON CREATE SET t.name = $tail_name "
            "SET t.name = coalesce(t.name, $tail_name) "
            f"MERGE (h)-[r:`{rel_type}` {{id: $id}}]->(t) "
            "SET r.confidence = $confidence, "
            "    r.attributes = $attributes, "
            "    r.head_id = $head_id, "
            "    r.tail_id = $tail_id, "
            "    r.head_name = $head_name, "
            "    r.tail_name = $tail_name, "
            "    r.sources = $sources "
            "RETURN r.id AS id"
        )
        result = session.run(
            query,
            id=rel.id,
            head_id=rel.head_id,
            tail_id=rel.tail_id,
            head_name=rel.head_name or "",
            tail_name=rel.tail_name or "",
            confidence=rel.confidence,
            attributes=attrs_for_neo4j(rel.attributes),
            sources=sources_for_neo4j(rel.sources),
        )
        return result.single() is not None

    def get_entity_by_name(self, name: str, entity_type: str | None = None) -> EntityRecord | None:
        with self._driver.session() as session:
            if entity_type:
                label = _validate_identifier(entity_type, "label")
                query = (
                    f"MATCH (e:`{label}`) WHERE toLower(e.name) = toLower($name) RETURN e LIMIT 1"
                )
            else:
                query = "MATCH (e) WHERE toLower(e.name) = toLower($name) RETURN e LIMIT 1"
            result = session.run(query, name=name)
            record = result.single()
            if not record:
                return None
            return node_to_entity(record["e"])

    def neighbors(
        self,
        entity_name: str,
        *,
        max_hops: int = 1,
        relation_types: list[str] | None = None,
        limit: int = 50,
    ) -> list[tuple[RelationRecord, EntityRecord]]:
        max_hops = max(1, min(max_hops, _MAX_NEIGHBOR_HOPS))
        limit = max(1, min(limit, _MAX_NEIGHBOR_LIMIT))
        params: dict[str, Any] = {"name": entity_name, "limit": limit, "max_hops": max_hops}
        if relation_types:
            for rt in relation_types:
                _validate_identifier(rt, "rel")
            params["rel_types"] = relation_types
        query = _neighbors_query(max_hops, relation_types)
        return self._run_neighbors(query, params, entity_name)

    def _run_neighbors(
        self, query: str, params: dict[str, Any], entity_name: str
    ) -> list[tuple[RelationRecord, EntityRecord]]:
        out: list[tuple[RelationRecord, EntityRecord]] = []
        with self._driver.session() as session:
            for record in session.run(query, **params):
                dst = node_to_entity(record["dst"])
                rel = rel_to_record(
                    record["r"],
                    walk_other_name=dst.name,
                    walk_from_name=entity_name,
                )
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
        """Bounded enumeration of simple paths (all, not only one shortest)."""
        max_hops = max(1, min(max_hops, _MAX_PATH_HOPS))
        limit = max(1, min(limit, _MAX_PATH_LIMIT))
        query = f"""
        MATCH (src), (dst)
        WHERE toLower(src.name) = toLower($source)
          AND toLower(dst.name) = toLower($target)
          AND src <> dst
        MATCH path = (src)-[*1..{max_hops}]-(dst)
        WHERE ALL(n IN nodes(path) WHERE size([m IN nodes(path) WHERE id(m) = id(n)]) = 1)
        RETURN path
        ORDER BY length(path) ASC
        LIMIT $limit
        """
        return self._run_paths(query, source=source_name, target=target_name, limit=limit)

    def _run_paths(
        self, query: str, *, source: str, target: str, limit: int
    ) -> list[PathRecord]:
        results: list[PathRecord] = []
        with self._driver.session() as session:
            for record in session.run(query, source=source, target=target, limit=limit):
                results.append(_path_record(record["path"]))
        return results

    def counts(self) -> dict[str, int]:
        with self._driver.session() as session:
            nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rels = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        return {"nodes": int(nodes), "relationships": int(rels)}


def _neighbors_query(max_hops: int, relation_types: list[str] | None) -> str:
    if max_hops == 1:
        return _one_hop_query(relation_types)
    last_rel_pred = (
        "AND type(last(relationships(path))) IN $rel_types" if relation_types else ""
    )
    return f"""
    MATCH (src)
    WHERE toLower(src.name) = toLower($name)
    MATCH path = (src)-[*1..{max_hops}]-(dst)
    WHERE src <> dst
      AND ALL(n IN nodes(path) WHERE size([m IN nodes(path) WHERE id(m) = id(n)]) = 1)
      {last_rel_pred}
    WITH dst, last(relationships(path)) AS r, length(path) AS hops
    WITH r, dst, min(hops) AS hops
    ORDER BY hops ASC, coalesce(r.id, '') ASC, dst.name ASC
    RETURN dst, r, hops
    LIMIT $limit
    """


def _one_hop_query(relation_types: list[str] | None) -> str:
    if relation_types:
        return """
        MATCH (src)-[r]-(dst)
        WHERE toLower(src.name) = toLower($name)
          AND type(r) IN $rel_types
          AND src <> dst
        RETURN dst, r, 1 AS hops
        ORDER BY coalesce(r.id, ''), dst.name
        LIMIT $limit
        """
    return """
    MATCH (src)-[r]-(dst)
    WHERE toLower(src.name) = toLower($name) AND src <> dst
    RETURN dst, r, 1 AS hops
    ORDER BY coalesce(r.id, ''), dst.name
    LIMIT $limit
    """


def _path_record(path: Any) -> PathRecord:
    nodes = [node_to_entity(n) for n in path.nodes]
    rels = [
        rel_to_record(
            r,
            walk_from_name=nodes[i].name if i < len(nodes) else "",
            walk_other_name=nodes[i + 1].name if i + 1 < len(nodes) else "",
        )
        for i, r in enumerate(path.relationships)
    ]
    return PathRecord(
        nodes=nodes,
        relations=rels,
        length=len(rels),
        score=1.0 / max(len(rels), 1),
    )
