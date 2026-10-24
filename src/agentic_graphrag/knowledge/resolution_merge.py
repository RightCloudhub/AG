"""Entity merge rewiring through explicit optional storage capabilities."""

from __future__ import annotations

from dataclasses import dataclass

from agentic_graphrag.core.ports.capabilities import EntityMutation
from agentic_graphrag.core.ports.stores import EntityRecord, GraphStore


@dataclass
class MergeApply:
    store: GraphStore
    keep: EntityRecord
    drop: EntityRecord
    canonical: str


def apply_entity_merge(req: MergeApply) -> None:
    """Merge within one tenant, rewiring edges before removing the alias entity."""
    if req.keep.tenant_id != req.drop.tenant_id:
        raise ValueError("Cannot merge entities from different tenants")
    if not isinstance(req.store, EntityMutation):
        raise TypeError("Graph backend does not support entity merge")
    req.keep.name = req.canonical
    req.keep.aliases = list(dict.fromkeys([*req.keep.aliases, req.drop.name, req.drop.id]))
    req.store.upsert_entities([req.keep])
    rewire_edges(req)
    delete_entity(req)


def rewire_edges(req: MergeApply) -> None:
    if not isinstance(req.store, EntityMutation):
        raise TypeError("Graph backend does not support entity merge")
    req.store.rewire_entity(
        req.drop.id, req.keep.id, keep_name=req.keep.name, tenant_id=req.keep.tenant_id
    )


def delete_entity(req: MergeApply) -> None:
    if not isinstance(req.store, EntityMutation):
        raise TypeError("Graph backend does not support entity merge")
    req.store.delete_entity(req.drop.id, tenant_id=req.drop.tenant_id)
