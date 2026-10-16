"""Store-level helpers for the review executor (backend-agnostic)."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from agentic_graphrag.core.ports.stores import GraphStore, RelationRecord

logger = logging.getLogger(__name__)


def all_relations(graph: GraphStore) -> list[RelationRecord]:
    """Every relation the store exposes (memory dict / list, or ``list_relations``)."""
    rels = getattr(graph, "_relations", None)
    if isinstance(rels, dict):
        return list(rels.values())
    if isinstance(rels, list):
        return list(rels)
    lister = getattr(graph, "list_relations", None)
    if callable(lister):
        return list(lister(limit=100_000) or [])
    return []


def delete_relations(
    graph: GraphStore, relation_ids: Iterable[str], *, tenant_id: str | None = None
) -> int:
    """Delete by id within one tenant; returns how many were removed."""
    deleter = getattr(graph, "delete_relation", None)
    if not callable(deleter):
        logger.warning("graph store %s cannot delete relations", type(graph).__name__)
        return 0
    removed = 0
    for rid in dict.fromkeys(relation_ids):
        try:
            removed += 1 if deleter(rid, tenant_id=tenant_id) else 0
        except Exception:  # noqa: BLE001
            logger.warning("delete_relation(%s) failed", rid)
    return removed


def relations_from_docs(graph: GraphStore, doc_ids: set[str], *, tenant_id: str = "") -> list[str]:
    """Ids of the tenant's relations whose every source is one of ``doc_ids``."""
    out: list[str] = []
    for rel in all_relations(graph):
        if rel.tenant_id != tenant_id:
            continue
        sources = [s for s in rel.sources or [] if s]
        if sources and all(s.get("doc_id") in doc_ids for s in sources):
            out.append(rel.id)
    return out
