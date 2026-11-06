"""Stable ID lookup and complete scoped scans for the memory graph."""

from agentic_graphrag.core.ports.stores import EntityRecord, RelationRecord
from agentic_graphrag.core.tenancy import tenant_matches


class MemoryGraphLookup:
    _entities: dict[str, EntityRecord]
    _relations: dict[str, RelationRecord]
    _by_entity_name: dict[tuple[str, str], list[EntityRecord]] | None

    def entity_names(self, *, tenant_id: str) -> list[str]:
        return list(
            dict.fromkeys(
                e.name for e in self._entities.values() if tenant_matches(e.tenant_id, tenant_id)
            )
        )

    def entities_named(self, name: str, *, tenant_id: str) -> list[EntityRecord]:
        key = (tenant_id or "default", name.casefold())
        return list(self._entity_name_index().get(key, ()))

    def _entity_name_index(self) -> dict[tuple[str, str], list[EntityRecord]]:
        if self._by_entity_name is None:
            index: dict[tuple[str, str], list[EntityRecord]] = {}
            for entity in self._entities.values():
                owner = entity.tenant_id or "default"
                for name in {entity.name.casefold(), *map(str.casefold, entity.aliases)}:
                    index.setdefault((owner, name), []).append(entity)
            self._by_entity_name = index
        return self._by_entity_name

    def get_entity_by_name(
        self, name: str, entity_type: str | None = None, *, tenant_id: str | None = None
    ) -> EntityRecord | None:
        target = name.lower()
        for entity in self._entities.values():
            if entity.name.lower() != target or not tenant_matches(entity.tenant_id, tenant_id):
                continue
            if entity_type is None or entity.type == entity_type:
                return entity
        return None

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
