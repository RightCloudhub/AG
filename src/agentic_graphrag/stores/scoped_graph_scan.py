"""Complete bound graph scans used by summaries, snapshots and ID-based traversal."""

from collections.abc import Iterator

from agentic_graphrag.core.ports.capabilities import RelationLister, RelationScanner
from agentic_graphrag.core.ports.stores import EntityRecord, GraphStore, RelationRecord
from agentic_graphrag.core.tenancy import tenant_matches

SCAN_PAGE_SIZE = 1000
LEGACY_SCAN_LIMIT = 100_000


def entities_in_scope(store: GraphStore, owner: str) -> Iterator[EntityRecord]:
    offset = 0
    while True:
        rows = store.list_entities(tenant_id=owner, limit=SCAN_PAGE_SIZE, offset=offset)
        yield from rows
        if len(rows) < SCAN_PAGE_SIZE:
            return
        offset += len(rows)


def relations_in_scope(store: GraphStore, owner: str) -> Iterator[RelationRecord]:
    if isinstance(store, RelationScanner):
        offset = 0
        while True:
            rows = store.scan_relations(tenant_id=owner, limit=SCAN_PAGE_SIZE, offset=offset)
            yield from rows
            if len(rows) < SCAN_PAGE_SIZE:
                return
            offset += len(rows)
    elif isinstance(store, RelationLister):
        rows = store.list_relations(limit=LEGACY_SCAN_LIMIT)
        if len(rows) >= LEGACY_SCAN_LIMIT:
            raise ValueError("Graph backend cannot provide a complete scoped relation scan")
        yield from (row for row in rows if tenant_matches(row.tenant_id, owner))
    else:
        raise TypeError("Graph backend does not support relation scans")
