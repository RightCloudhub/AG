"""Entity merge rewiring helpers (keeps resolution.py under size budget)."""

from __future__ import annotations

from dataclasses import dataclass

from agentic_graphrag.stores.interfaces import EntityRecord, GraphStore


@dataclass
class MergeApply:
    store: GraphStore
    keep: EntityRecord
    drop: EntityRecord
    canonical: str


def apply_entity_merge(req: MergeApply) -> None:
    """Merge drop into keep: alias, rewire edges, then delete the drop entity."""
    aliases = list(dict.fromkeys([*(req.keep.aliases or []), req.drop.name, req.drop.id]))
    req.keep.name = req.canonical
    req.keep.aliases = aliases
    req.store.upsert_entities([req.keep])
    rewire_edges(req)
    delete_entity(req)


def rewire_edges(req: MergeApply) -> None:
    """Point relations that referenced drop at keep (memory list or store API)."""
    rewire = getattr(req.store, "rewire_entity", None)
    if callable(rewire):
        try:
            rewire(req.drop.id, req.keep.id, keep_name=req.keep.name)
            return
        except Exception:
            pass
    rels = getattr(req.store, "_relations", None)
    if not isinstance(rels, list):
        return
    for r in rels:
        if r.head_id == req.drop.id:
            r.head_id = req.keep.id
            r.head_name = req.keep.name
        if r.tail_id == req.drop.id:
            r.tail_id = req.keep.id
            r.tail_name = req.keep.name
    by_name = getattr(req.store, "_by_name", None)
    entities = getattr(req.store, "_entities", None)
    if isinstance(by_name, dict) and isinstance(entities, dict):
        by_name[req.keep.name.lower()] = req.keep
        entities[req.keep.id] = req.keep


def delete_entity(req: MergeApply) -> None:
    deleter = getattr(req.store, "delete_entity", None)
    if callable(deleter):
        try:
            deleter(req.drop.id)
            return
        except Exception:
            pass
    entities = getattr(req.store, "_entities", None)
    by_name = getattr(req.store, "_by_name", None)
    if isinstance(entities, dict):
        entities.pop(req.drop.id, None)
    if not isinstance(by_name, dict):
        return
    by_name.pop(req.drop.name.lower(), None)
    for k, ent in list(by_name.items()):
        if getattr(ent, "id", None) == req.drop.id:
            by_name.pop(k, None)
