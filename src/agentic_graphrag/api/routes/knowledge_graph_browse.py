"""Tenant-scoped graph browsing through the typed entity-listing capability."""

from __future__ import annotations

from dataclasses import dataclass, field

from agentic_graphrag.core.constants import DEFAULT_PAGE_SIZE
from agentic_graphrag.core.ports.capabilities import EntityLister
from agentic_graphrag.core.ports.stores import EntityRecord

MAX_ENTITY_PAGE = 500
DEFAULT_ENTITY_PAGE = DEFAULT_PAGE_SIZE
MAX_ENTITY_SCAN = 5000


@dataclass(frozen=True)
class EntityPage:
    records: list[EntityRecord] = field(default_factory=list)
    total: int = 0
    total_is_floor: bool = False


def entity_page(
    store: object,
    *,
    limit: int,
    offset: int,
    tenant_id: str | None,
    query: str = "",
    entity_type: str = "",
) -> EntityPage:
    """Filter the tenant and search criteria before slicing the page."""
    rows = (
        store.list_entities(limit=MAX_ENTITY_SCAN, tenant_id=tenant_id)
        if isinstance(store, EntityLister)
        else []
    )
    filtered = _filter_entities(
        filter_tenant(rows, tenant_id), query=query, entity_type=entity_type
    )
    return EntityPage(
        records=filtered[offset : offset + limit],
        total=len(filtered),
        total_is_floor=len(rows) >= MAX_ENTITY_SCAN,
    )


def _filter_entities(
    rows: list[EntityRecord], *, query: str, entity_type: str
) -> list[EntityRecord]:
    needle, selected_type = query.casefold(), entity_type.casefold()
    return [
        row for row in rows if _matches_search(row, needle) and _matches_type(row, selected_type)
    ]


def _matches_search(row: EntityRecord, needle: str) -> bool:
    return not needle or any(
        needle in value.casefold() for value in [row.name, row.id, *row.aliases]
    )


def _matches_type(row: EntityRecord, selected_type: str) -> bool:
    return not selected_type or row.type.casefold() == selected_type


def filter_tenant(items: list[EntityRecord], tenant_id: str | None) -> list[EntityRecord]:
    if tenant_id is None:
        return items
    return [item for item in items if not item.tenant_id or item.tenant_id == tenant_id]
