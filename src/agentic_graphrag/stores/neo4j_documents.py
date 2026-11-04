"""Transactional, tenant-scoped provenance deletion for Neo4j."""

from collections.abc import Callable
from typing import Any

from neo4j import Driver, ManagedTransaction

from agentic_graphrag.core.ports.stores import ChunkIdentity, ChunkKey
from agentic_graphrag.stores.document_provenance import obsolete_support
from agentic_graphrag.stores.neo4j_codec import sources_for_neo4j, sources_from_neo4j

SourcePredicate = Callable[[dict[str, Any]], bool]

_SCOPE = (
    "(coalesce(r.tenant_id, '') = $tenant_id "
    "OR ($tenant_id = 'default' AND coalesce(r.tenant_id, '') = ''))"
)


class Neo4jDocumentDeletion:
    _driver: Driver

    def delete_by_doc(self, doc_id: str, tenant_id: str) -> int:
        with self._driver.session() as session:
            return session.execute_write(
                _drop_sources, tenant_id or "default", lambda s: s.get("doc_id") == doc_id
            )

    def delete_chunk_support(self, key: ChunkKey, *, below: ChunkIdentity | None = None) -> int:
        """Transactionally remove one chunk's provenance; shared facts survive."""
        with self._driver.session() as session:
            return session.execute_write(
                _drop_sources, key.tenant_id or "default", lambda s: obsolete_support(s, key, below)
            )


def _drop_sources(tx: ManagedTransaction, tenant_id: str, doomed: SourcePredicate) -> int:
    deleted = 0
    rows = list(
        tx.run(
            f"MATCH ()-[r]->() WHERE {_SCOPE} RETURN elementId(r) AS eid, r.sources AS sources",
            tenant_id=tenant_id,
        )
    )
    for row in rows:
        original = sources_from_neo4j(row["sources"])
        sources = [source for source in original if not doomed(source)]
        if len(sources) == len(original):
            continue
        action = "SET r.sources = $sources" if sources else "DELETE r"
        tx.run(
            f"MATCH ()-[r]->() WHERE elementId(r)=$eid AND {_SCOPE} {action}",
            eid=row["eid"],
            tenant_id=tenant_id,
            sources=sources_for_neo4j(sources),
        ).consume()
        deleted += int(not sources)
    return deleted
