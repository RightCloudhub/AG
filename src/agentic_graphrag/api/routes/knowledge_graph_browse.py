"""Graph browse helpers for ``GET /v1/graph/entities`` (P5-CAP-01).

Resolves entity listings across GraphStore implementations without widening the
store Protocol, and applies tenant scoping **before** pagination. Filtering a
page after slicing it produced short — sometimes empty — pages that were not the
end of the result set, and reported the store's global row count as the tenant's
total (docs/BUSINESS_LOGIC.md BL-09).
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

MAX_ENTITY_PAGE = 500
DEFAULT_ENTITY_PAGE = 50
# Ceiling on rows read to build one page. Paging is applied to the filtered set,
# which means the filter has to see more rows than the page holds; this bounds
# how many. A store with more matching rows than this reports a floor total.
MAX_ENTITY_SCAN = 5000


@dataclass(frozen=True)
class EntityPage:
    """One page of entity records plus the filtered total it was taken from."""

    records: list = field(default_factory=list)
    total: int = 0
    # True when the scan hit MAX_ENTITY_SCAN, so ``total`` is a floor, not the
    # real count. Callers must not present it as exact.
    total_is_floor: bool = False


def entity_page(store: object, *, limit: int, offset: int, tenant_id: str | None) -> EntityPage:
    """Resolve one tenant-scoped page from whichever access shape the store offers.

    Every branch filters before it slices, so ``records`` is a real page of the
    tenant's rows and ``total`` counts the same set.
    """
    lister = getattr(store, "list_entities", None)
    if callable(lister) and _accepts(lister, "tenant_id"):
        rows = _lister_rows(lister, tenant_id=tenant_id)  # store filters; page locally
    elif callable(lister):
        rows = filter_tenant(_lister_rows(lister, tenant_id=None), tenant_id)
    else:
        rows = filter_tenant(_rows_from_attrs(store), tenant_id)
    return _slice_page(rows, limit=limit, offset=offset)


def _slice_page(rows: list, *, limit: int, offset: int) -> EntityPage:
    return EntityPage(
        records=rows[offset : offset + limit],
        total=len(rows),
        total_is_floor=len(rows) >= MAX_ENTITY_SCAN,
    )


def _accepts(lister: Any, name: str) -> bool:
    """Whether ``lister`` takes keyword ``name``.

    Signature inspection, not ``except TypeError`` around the call: a TypeError
    raised *inside* a store implementation would otherwise be misread as an old
    signature and silently retried with fewer arguments.
    """
    try:
        params = inspect.signature(lister).parameters
    except (TypeError, ValueError):
        return False
    if name in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _lister_rows(lister: Any, *, tenant_id: str | None) -> list:
    """Call ``list_entities`` with only the keywords its signature accepts."""
    kwargs: dict[str, Any] = {}
    if _accepts(lister, "limit"):
        kwargs["limit"] = MAX_ENTITY_SCAN
    if _accepts(lister, "offset"):
        kwargs["offset"] = 0
    if tenant_id is not None and _accepts(lister, "tenant_id"):
        kwargs["tenant_id"] = tenant_id
    return list(lister(**kwargs))


def _rows_from_attrs(store: object) -> list:
    """Fallback for stores exposing an entity map instead of ``list_entities``."""
    for attr in ("entities", "_entities"):
        items = _items_from_attr(store, attr)
        if items is not None:
            return items
    return []


def _items_from_attr(store: object, attr: str) -> list | None:
    entities = getattr(store, attr, None)
    if isinstance(entities, dict):
        items = list(entities.values())
        items.sort(key=lambda e: (getattr(e, "type", ""), getattr(e, "name", "").lower()))
        return items
    if isinstance(entities, list):
        return list(entities)
    return None


def filter_tenant(items: list, tenant_id: str | None) -> list:
    """Drop records owned by another tenant (records without a tenant are shared)."""
    if tenant_id is None:
        return items
    return [e for e in items if (getattr(e, "tenant_id", None) or tenant_id) == tenant_id]
