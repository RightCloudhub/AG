"""Stable ID lookup and complete scoped scans for the memory graph."""

from agentic_graphrag.core.ports.stores import EntityRecord, RelationRecord
from agentic_graphrag.core.tenancy import tenant_matches


class MemoryGraphLookup:
    _entities: dict[str, EntityRecord]
    _relations: dict[str, RelationRecord]

    def scan_relations(
        self, *, tenant_id: str, limit: int = 1000, offset: int = 0
    ) -> list[RelationRecord]:
        rows = sorted(
            (r for r in self._relations.values() if tenant_matches(r.tenant_id, tenant_id)),
            key=lambda record: record.id,
        )
        return rows[max(0, offset) : max(0, offset) + max(0, limit)]

    def get_entity_by_id(self, entity_id: str, *, tenant_id: str) -> EntityRecord | None:
        return next(
            (
                e
                for e in self._entities.values()
                if e.id == entity_id and tenant_matches(e.tenant_id, tenant_id)
            ),
            None,
        )
