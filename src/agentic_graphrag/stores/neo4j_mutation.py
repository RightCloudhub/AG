"""Tenant-scoped entity rewiring for the Neo4j adapter."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from neo4j import Driver, ManagedTransaction

from agentic_graphrag.core.ports.capabilities import (
    DEFAULT_RELATION_SCAN,
    EntityMutation,
    RelationDeletion,
    RelationLister,
)
from agentic_graphrag.core.ports.stores import RelationRecord
from agentic_graphrag.stores.neo4j_codec import rel_to_record

_SAFE_RELATION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RELATION_TYPES = """
MATCH (drop)-[edge]-()
WHERE drop.id = $drop_id AND coalesce(drop.tenant_id, '') = $tenant_id
  AND coalesce(edge.tenant_id, '') = $tenant_id
RETURN DISTINCT type(edge) AS kind
"""
_DELETE_ENTITY = """
MATCH (entity)
WHERE entity.id = $entity_id AND coalesce(entity.tenant_id, '') = $tenant_id
DELETE entity
"""


@dataclass(frozen=True)
class _RewireRequest:
    drop_id: str
    keep_id: str
    keep_name: str
    tenant_id: str


class Neo4jEntityMutation(EntityMutation, RelationLister, RelationDeletion):
    _driver: Driver

    def rewire_entity(self, drop_id: str, keep_id: str, *, keep_name: str, tenant_id: str) -> None:
        request = _RewireRequest(drop_id, keep_id, keep_name, tenant_id)
        with self._driver.session() as session:
            session.execute_write(_rewire_transaction, request)

    def delete_entity(self, entity_id: str, *, tenant_id: str) -> None:
        with self._driver.session() as session:
            session.run(_DELETE_ENTITY, entity_id=entity_id, tenant_id=tenant_id).consume()

    def list_relations(self, limit: int = DEFAULT_RELATION_SCAN) -> list[RelationRecord]:
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
        """Delete a relationship by id; ``tenant_id`` scopes it to one tenant's copy."""
        if not relation_id:
            return False
        with self._driver.session() as session:
            row = session.run(
                "MATCH ()-[r]->() WHERE r.id = $id "
                "AND ($tenant_id IS NULL OR coalesce(r.tenant_id, '') = $tenant_id) "
                "DELETE r RETURN count(*) AS c",
                id=relation_id,
                tenant_id=tenant_id,
            ).single()
        return bool(row and int(row["c"]) > 0)


def _rewire_transaction(tx: ManagedTransaction, request: _RewireRequest) -> None:
    params = asdict(request)
    types = [str(row["kind"]) for row in tx.run(_RELATION_TYPES, **params)]
    for kind in types:
        for query in rewire_queries(kind):
            tx.run(query, **params).consume()


def rewire_queries(kind: str) -> tuple[str, str]:
    """Only relationship identifiers are interpolated; every value is a parameter."""
    if not _SAFE_RELATION.fullmatch(kind):
        raise ValueError(f"Invalid relation identifier: {kind!r}")
    match = """
MATCH (keep), (drop)
WHERE keep.id = $keep_id AND coalesce(keep.tenant_id, '') = $tenant_id
  AND drop.id = $drop_id AND coalesce(drop.tenant_id, '') = $tenant_id
"""
    outgoing = (
        match
        + f"""
MATCH (drop)-[edge:`{kind}`]->(tail)
WHERE coalesce(edge.tenant_id, '') = $tenant_id
WITH keep, edge, CASE WHEN tail = drop THEN keep ELSE tail END AS destination
CREATE (keep)-[replacement:`{kind}`]->(destination)
SET replacement = properties(edge), replacement.head_id = $keep_id,
    replacement.head_name = $keep_name,
    replacement.tail_id = CASE WHEN destination = keep THEN $keep_id ELSE edge.tail_id END,
    replacement.tail_name = CASE WHEN destination = keep THEN $keep_name ELSE edge.tail_name END
DELETE edge
"""
    )
    incoming = (
        match
        + f"""
MATCH (head)-[edge:`{kind}`]->(drop)
WHERE coalesce(edge.tenant_id, '') = $tenant_id
CREATE (head)-[replacement:`{kind}`]->(keep)
SET replacement = properties(edge), replacement.tail_id = $keep_id,
    replacement.tail_name = $keep_name
DELETE edge
"""
    )
    return outgoing, incoming
