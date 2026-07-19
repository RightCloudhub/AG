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

import json
import re
from typing import Any

from neo4j import Driver, GraphDatabase

from agentic_graphrag.stores.interfaces import EntityRecord, PathRecord, RelationRecord

_SAFE_LABEL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_REL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(value: str, kind: str) -> str:
    pattern = _SAFE_LABEL if kind == "label" else _SAFE_REL
    if not pattern.match(value):
        raise ValueError(f"Invalid {kind} identifier: {value!r}")
    return value


def _attrs_for_neo4j(attributes: dict[str, Any] | None) -> str:
    """Serialize attributes as JSON string (Neo4j forbids nested Map properties)."""
    return json.dumps(attributes or {}, ensure_ascii=False, sort_keys=True)


def _attrs_from_neo4j(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
            return dict(val) if isinstance(val, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _sources_for_neo4j(sources: list[dict[str, Any]] | None) -> str:
    """Serialize sources list as JSON string (same nested-map constraint)."""
    return json.dumps(list(sources or []), ensure_ascii=False, sort_keys=True)


def _sources_from_neo4j(raw: Any) -> list[dict[str, Any]]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [dict(x) for x in raw if isinstance(x, dict)]
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
            if isinstance(val, list):
                return [dict(x) for x in val if isinstance(x, dict)]
        except json.JSONDecodeError:
            return []
    return []


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
                label = _validate_identifier(ent.type, "label")
                # Label is validated; properties are parameterized.
                query = (
                    f"MERGE (e:`{label}` {{id: $id}}) "
                    "SET e.name = $name, e.attributes = $attributes, e.aliases = $aliases "
                    "RETURN e.id AS id"
                )
                result = session.run(
                    query,
                    id=ent.id,
                    name=ent.name,
                    attributes=_attrs_for_neo4j(ent.attributes),
                    aliases=list(ent.aliases or []),
                )
                if result.single() is not None:
                    count += 1
        return count

    def upsert_relations(self, relations: list[RelationRecord]) -> int:
        """Upsert relations; return count of relationships actually merged.

        Endpoints are ``MERGE``d as placeholders when missing (name + id only),
        matching in-memory semantics where relation rows can exist without a
        prior full entity upsert. Relation properties store semantic
        head/tail and sources for direction-correct reads.
        """
        if not relations:
            return 0
        count = 0
        with self._driver.session() as session:
            for rel in relations:
                rel_type = _validate_identifier(rel.type, "rel")
                # MERGE endpoints by id (placeholder ON CREATE) then directed edge by id.
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
                    attributes=_attrs_for_neo4j(rel.attributes),
                    sources=_sources_for_neo4j(rel.sources),
                )
                if result.single() is not None:
                    count += 1
        return count

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
        params: dict[str, Any] = {"name": entity_name, "limit": limit, "max_hops": max_hops}
        if relation_types:
            for rt in relation_types:
                _validate_identifier(rt, "rel")
            params["rel_types"] = relation_types

        # 1-hop: stable ORDER BY r.id
        if max_hops == 1:
            if relation_types:
                query = """
                MATCH (src)-[r]-(dst)
                WHERE toLower(src.name) = toLower($name)
                  AND type(r) IN $rel_types
                  AND src <> dst
                RETURN dst, r, 1 AS hops
                ORDER BY coalesce(r.id, ''), dst.name
                LIMIT $limit
                """
            else:
                query = """
                MATCH (src)-[r]-(dst)
                WHERE toLower(src.name) = toLower($name) AND src <> dst
                RETURN dst, r, 1 AS hops
                ORDER BY coalesce(r.id, ''), dst.name
                LIMIT $limit
                """
        else:
            # Multi-hop: last edge of each simple path, de-dup by r, ORDER BY hops, r.id
            last_rel_pred = (
                "AND type(last(relationships(path))) IN $rel_types" if relation_types else ""
            )
            query = f"""
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

        out: list[tuple[RelationRecord, EntityRecord]] = []
        with self._driver.session() as session:
            for record in session.run(query, **params):
                dst = _node_to_entity(record["dst"])
                # Prefer persisted semantic endpoints over undirected walk orientation.
                rel = _rel_to_record(
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
        max_hops = max(1, min(max_hops, 6))
        limit = max(1, min(limit, 50))
        # Variable-length bound is integer-inlined after validation (not user string).
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
        results: list[PathRecord] = []
        with self._driver.session() as session:
            for record in session.run(query, source=source_name, target=target_name, limit=limit):
                path = record["path"]
                nodes = [_node_to_entity(n) for n in path.nodes]
                rels = [
                    _rel_to_record(
                        r,
                        walk_from_name=nodes[i].name if i < len(nodes) else "",
                        walk_other_name=nodes[i + 1].name if i + 1 < len(nodes) else "",
                    )
                    for i, r in enumerate(path.relationships)
                ]
                results.append(
                    PathRecord(
                        nodes=nodes,
                        relations=rels,
                        length=len(rels),
                        score=1.0 / max(len(rels), 1),
                    )
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
        attributes=_attrs_from_neo4j(props.get("attributes")),
        aliases=list(props.get("aliases") or []),
    )


def _rel_to_record(
    rel: Any,
    *,
    walk_from_name: str = "",
    walk_other_name: str = "",
    head_name: str = "",
    tail_name: str = "",
) -> RelationRecord:
    """Build RelationRecord with true head/tail when properties exist.

    Legacy edges without ``head_name``/``tail_name`` fall back to walk
    orientation (``walk_from`` → ``walk_other``) or explicit head/tail args.
    """
    props = dict(rel)
    stored_head = str(props.get("head_name") or "").strip()
    stored_tail = str(props.get("tail_name") or "").strip()
    if stored_head or stored_tail:
        h_name = stored_head or head_name or walk_from_name
        t_name = stored_tail or tail_name or walk_other_name
    elif head_name or tail_name:
        h_name = head_name
        t_name = tail_name
    else:
        # Undirected walk: treat walk_from as head only when props missing (legacy).
        h_name = walk_from_name
        t_name = walk_other_name

    return RelationRecord(
        id=str(props.get("id", f"{rel.type}:{h_name}->{t_name}")),
        type=rel.type,
        head_id=str(props.get("head_id", "")),
        tail_id=str(props.get("tail_id", "")),
        head_name=h_name,
        tail_name=t_name,
        confidence=float(props.get("confidence", 1.0)),
        attributes=_attrs_from_neo4j(props.get("attributes")),
        sources=_sources_from_neo4j(props.get("sources")),
    )
