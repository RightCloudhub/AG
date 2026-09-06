"""Review decision executor (BL-03): decisions produce graph side effects.

``ReviewQueue.decide`` only records the decision — approving a conflict used to
change nothing in the graph, making the queue a write-only ledger rather than a
control plane (docs/BUSINESS_LOGIC.md BL-03). This module applies the side
effects a decision implies, from the payload the incremental updater enqueued
(``Conflict.to_dict()``) or any payload carrying a ``triple`` / ``incoming``
object.

Semantics:
- conflict + approve → write the incoming triple, retire the superseded edges;
- conflict + reject → keep the incumbent edge (nothing to change);
- extraction / spotcheck with a triple + approve → upsert it;
- extraction / spotcheck with a triple + reject → delete it from the graph.
"""

from __future__ import annotations

import logging
from typing import Any

from agentic_graphrag.knowledge.graph_builder import triples_to_records
from agentic_graphrag.knowledge.review.queue import ReviewDecision, ReviewItem, ReviewType
from agentic_graphrag.knowledge.schema_check import Triple
from agentic_graphrag.stores.interfaces import GraphStore

logger = logging.getLogger(__name__)


class ReviewExecutor:
    """Apply approved / rejected review decisions to the graph store."""

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def apply(self, item: ReviewItem, decision: str) -> dict[str, Any]:
        """Apply the side effects for a just-decided item.

        Data problems never raise — the decision is already recorded, and the
        route reports the executor outcome alongside it.
        """
        if decision == ReviewDecision.APPROVE.value:
            return self._approve(item)
        if decision == ReviewDecision.REJECT.value:
            return self._reject(item)
        return {"applied": False, "reason": f"decision {decision} has no graph effect"}

    def _approve(self, item: ReviewItem) -> dict[str, Any]:
        triple = _payload_triple(item)
        if triple is None:
            return {"applied": False, "reason": "payload carries no triple"}
        entities, relations = triples_to_records([triple])
        self.store.upsert_entities(entities)
        written = self.store.upsert_relations(relations)
        retired = self._retire_superseded(item)
        return {"applied": True, "action": "upsert", "relations": written, "retired": retired}

    def _reject(self, item: ReviewItem) -> dict[str, Any]:
        triple = _payload_triple(item)
        if triple is None:
            return {"applied": False, "reason": "payload carries no triple"}
        if item.type == ReviewType.CONFLICT.value:
            # Rejecting a proposed replacement keeps the incumbent edge.
            return {"applied": False, "action": "keep_existing", "reason": "conflict rejected"}
        removed = self._delete_by_triple(triple)
        return {"applied": True, "action": "delete", "relations": removed}

    def _retire_superseded(self, item: ReviewItem) -> int:
        retired = 0
        for row in item.payload.get("superseded") or []:
            if isinstance(row, dict) and self._delete(str(row.get("id") or "")):
                retired += 1
        return retired

    def _delete_by_triple(self, triple: Triple) -> int:
        _entities, relations = triples_to_records([triple])
        removed = 0
        for record in relations:
            if self._delete(record.id):
                removed += 1
        return removed

    def _delete(self, relation_id: str) -> bool:
        if not relation_id:
            return False
        deleter = getattr(self.store, "delete_relation", None)
        if not callable(deleter):
            logger.warning("graph store cannot delete relations; %s survives", relation_id)
            return False
        try:
            return bool(deleter(relation_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("delete_relation(%s) failed: %s", relation_id, exc)
            return False


def _payload_triple(item: ReviewItem) -> Triple | None:
    """Parse the payload's triple, from either key the producers use."""
    raw = item.payload.get("incoming") or item.payload.get("triple")
    if not isinstance(raw, dict):
        return None
    try:
        return Triple.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Review item %s carries an unparseable triple: %s", item.id, exc)
        return None
