"""Graph browse APIs for the knowledge-graph explorer view (P5-UI-02).

Thin read-only endpoints over the active ``GraphStore``: relations list,
single-entity detail, and one-hop neighbors. The offline in-memory store is
the default backend; live Neo4j degrades to empty lists where an enumeration
method is absent. RBAC matches ``/v1/graph/entities`` (admin|operator).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from agentic_graphrag.api.envelope import MetaBody, ok
from agentic_graphrag.api.errors import INVALID_INPUT, ApiError
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.knowledge import _principal, _service
from agentic_graphrag.stores.interfaces import EntityRecord, RelationRecord

router = APIRouter(prefix="/v1", tags=["graph-browse"])

DEFAULT_NEIGHBORS_LIMIT = 100
MAX_NEIGHBORS_LIMIT = 200
DEFAULT_RELATIONS_LIMIT = 500
MAX_RELATIONS_LIMIT = 5000


def _store(request: Request) -> Any:
    return _service(request).bundle.graph


def _entity_row(e: EntityRecord) -> dict[str, Any]:
    return {
        "id": e.id,
        "name": e.name,
        "type": e.type,
        "aliases": list(getattr(e, "aliases", None) or []),
        "attributes": dict(getattr(e, "attributes", None) or {}),
        "sources": list(getattr(e, "sources", None) or []),
    }


def _relation_row(r: RelationRecord) -> dict[str, Any]:
    return {
        "id": r.id,
        "type": r.type,
        "head_name": r.head_name,
        "tail_name": r.tail_name,
        "confidence": r.confidence,
    }


@router.get(
    "/graph/relations",
    dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))],
)
def list_graph_relations(
    request: Request,
    limit: int = Query(DEFAULT_RELATIONS_LIMIT),
    offset: int = Query(0),
) -> dict:
    """Browse relation records (read-only). Unsupported stores yield empty."""
    store = _store(request)
    lim = min(MAX_RELATIONS_LIMIT, max(1, int(limit or DEFAULT_RELATIONS_LIMIT)))
    lister = getattr(store, "list_relations", None)
    if not callable(lister):
        return ok([], meta=MetaBody(total=0, limit=lim, page=1))
    tenant_id, _user_id = _principal(request)
    try:
        records = list(lister(limit=lim, tenant_id=tenant_id))
    except TypeError:
        records = list(lister(limit=lim))
    rows = [_relation_row(r) for r in records]
    return ok(rows, meta=MetaBody(total=len(rows), limit=lim, page=offset // max(lim, 1) + 1))


@router.get(
    "/graph/entities/{name}",
    dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))],
)
def get_graph_entity(name: str, request: Request) -> dict:
    """Single entity detail with attributes/sources; 404 when unknown."""
    store = _store(request)
    lookup = getattr(store, "get_entity_by_name", None)
    entity = lookup(name) if callable(lookup) else None
    if entity is None:
        raise ApiError(INVALID_INPUT, f"Unknown entity: {name}", status_code=404)
    return ok(_entity_row(entity))


@router.get(
    "/graph/entities/{name}/neighbors",
    dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))],
)
def list_graph_neighbors(
    name: str,
    request: Request,
    max_hops: int = Query(1),
    limit: int = Query(DEFAULT_NEIGHBORS_LIMIT),
) -> dict:
    """One-hop neighbors of an entity as (relation, entity) pairs."""
    store = _store(request)
    neighbor_fn = getattr(store, "neighbors", None)
    if not callable(neighbor_fn):
        return ok([], meta=MetaBody(total=0, limit=limit, page=1))
    tenant_id, _user_id = _principal(request)
    try:
        pairs = neighbor_fn(
            name,
            max_hops=max(1, max_hops),
            limit=min(MAX_NEIGHBORS_LIMIT, max(1, limit)),
            tenant_id=tenant_id,
        )
    except TypeError:
        pairs = neighbor_fn(name, max_hops=max(1, max_hops), limit=limit)
    rows = [
        {
            "relation": _relation_row(rel),
            "entity": _entity_row(entity),
        }
        for rel, entity in pairs
    ]
    return ok(rows, meta=MetaBody(total=len(rows), limit=limit, page=1))
