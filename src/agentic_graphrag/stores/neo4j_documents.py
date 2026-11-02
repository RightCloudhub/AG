"""Transactional, tenant-scoped provenance deletion for Neo4j."""

from neo4j import Driver, ManagedTransaction

from agentic_graphrag.stores.neo4j_codec import sources_for_neo4j, sources_from_neo4j

_SCOPE = (
    "(coalesce(r.tenant_id, '') = $tenant_id "
    "OR ($tenant_id = 'default' AND coalesce(r.tenant_id, '') = ''))"
)


class Neo4jDocumentDeletion:
    _driver: Driver

    def delete_by_doc(self, doc_id: str, tenant_id: str) -> int:
        with self._driver.session() as session:
            return session.execute_write(_delete_document, doc_id, tenant_id or "default")


def _delete_document(tx: ManagedTransaction, doc_id: str, tenant_id: str) -> int:
    deleted = 0
    rows = list(
        tx.run(
            f"MATCH ()-[r]->() WHERE {_SCOPE} RETURN elementId(r) AS eid, r.sources AS sources",
            tenant_id=tenant_id,
        )
    )
    for row in rows:
        original = sources_from_neo4j(row["sources"])
        sources = [source for source in original if source.get("doc_id") != doc_id]
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
