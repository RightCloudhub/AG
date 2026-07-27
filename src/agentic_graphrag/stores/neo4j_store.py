"""Neo4j GraphStore with parameterized Cypher only (NFR-07).

Relation props store semantic endpoints + sources JSON for true direction.
Multi-hop neighbors ORDER BY hops/id; paths are bounded simple-path scans.
Pre-property graphs must be rebuilt via ``agr-build-graph``.
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
from agentic_graphrag.stores.neo4j_queries import neighbors_query, path_record

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
            "SET e.name = $name, e.tenant_id = $tenant_id, "
            "e.attributes = $attributes, e.aliases = $aliases "
            "RETURN e.id AS id"
        )
        result = session.run(
            query,
            id=ent.id,
            name=ent.name,
            tenant_id=ent.tenant_id,
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
            "    r.tenant_id = $tenant_id, "
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
            tenant_id=rel.tenant_id,
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
        tenant_id: str | None = None,
    ) -> list[tuple[RelationRecord, EntityRecord]]:
        max_hops = max(1, min(max_hops, _MAX_NEIGHBOR_HOPS))
        limit = max(1, min(limit, _MAX_NEIGHBOR_LIMIT))
        params: dict[str, Any] = {
            "name": entity_name,
            "limit": limit,
            "max_hops": max_hops,
            "tenant_id": tenant_id,
        }
        if relation_types:
            for rt in relation_types:
                _validate_identifier(rt, "rel")
            params["rel_types"] = relation_types
        query = neighbors_query(max_hops, relation_types)
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
        tenant_id: str | None = None,
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
          AND ($tenant_id IS NULL OR ALL(r IN relationships(path)
              WHERE coalesce(r.tenant_id, '') IN [$tenant_id, '']))
        RETURN path
        ORDER BY length(path) ASC
        LIMIT $limit
        """
        return self._run_paths(
            query, source=source_name, target=target_name, limit=limit, tenant_id=tenant_id
        )

    def _run_paths(
        self,
        query: str,
        *,
        source: str,
        target: str,
        limit: int,
        tenant_id: str | None,
    ) -> list[PathRecord]:
        results: list[PathRecord] = []
        with self._driver.session() as session:
            for record in session.run(
                query, source=source, target=target, limit=limit, tenant_id=tenant_id
            ):
                results.append(path_record(record["path"]))
        return results

    def counts(self) -> dict[str, int]:
        with self._driver.session() as session:
            nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rels = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        return {"nodes": int(nodes), "relationships": int(rels)}

    def list_relations(self, limit: int = 50_000) -> list[RelationRecord]:
        """Enumerate relations for incremental conflict indexing."""
        lim = max(1, min(int(limit), 100_000))
        q = "MATCH (h)-[r]->(t) RETURN r, h, t LIMIT $limit"
        out: list[RelationRecord] = []
        with self._driver.session() as session:
            for rec in session.run(q, limit=lim):
                out.append(
                    rel_to_record(
                        rec["r"],
                        walk_other_name=rec["t"].get("name") or "",
                        walk_from_name=rec["h"].get("name") or "",
                    )
                )
        return out

    def delete_relation(self, relation_id: str, *, tenant_id: str | None = None) -> bool:
        """Delete a relationship by property id (conflict supersede).

        When ``tenant_id`` is given, only deletes the relationship in that
        tenant's scope. ``_relation_id`` is tenant-agnostic, so without this
        scope the same id would delete across all tenants.
        """
        if not relation_id:
            return False
        with self._driver.session() as session:
            if tenant_id is not None:
                row = session.run(
                    "MATCH ()-[r]->() WHERE r.id = $id AND r.tenant_id = $tenant "
                    "DELETE r RETURN count(*) AS c",
                    id=relation_id,
                    tenant=tenant_id,
                ).single()
            else:
                row = session.run(
                    "MATCH ()-[r]->() WHERE r.id = $id DELETE r RETURN count(*) AS c",
                    id=relation_id,
                ).single()
        return bool(row and int(row["c"]) > 0)
