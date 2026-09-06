"""BL-03: review decisions produce graph side effects (ReviewExecutor)."""

from __future__ import annotations

import json

from agentic_graphrag.knowledge.graph_builder import triples_to_records
from agentic_graphrag.knowledge.incremental import IncrementalUpdater
from agentic_graphrag.knowledge.review.executor import ReviewExecutor
from agentic_graphrag.knowledge.review.queue import ReviewDecision, ReviewQueue, ReviewType
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore


def _triple(head: str, tail: str, conf: float = 0.9) -> Triple:
    return Triple(
        head=EntityMention(name=head, type="Person"),
        relation="WORKED_AT",
        tail=EntityMention(name=tail, type="Company"),
        confidence=conf,
    )


def _seed(store: InMemoryGraphStore, head: str, tail: str, conf: float = 0.9):
    _ents, rels = triples_to_records([_triple(head, tail, conf)])
    store.upsert_entities(_ents)
    store.upsert_relations(rels)
    return rels[0]


def _conflict_item(store: InMemoryGraphStore, *, new_tail: str = "Acme", conf: float = 0.99):
    """Produce a realistic conflict review item through the incremental path."""
    _seed(store, "Alice", "Zed Corp", 0.95)
    queue = ReviewQueue()
    updater = IncrementalUpdater(store, review_queue=queue)
    updater.apply_batch([_triple("Alice", new_tail, conf)])
    items = queue.list(type=ReviewType.CONFLICT.value)
    assert items, "expected a REVIEW conflict to be enqueued"
    return items[0], queue


def _edge_ids(store: InMemoryGraphStore) -> set[str]:
    return {r.id for r in store._relations.values()}


def _tail_names(store: InMemoryGraphStore) -> set[str]:
    return {r.tail_name for r in store._relations.values()}


class TestConflictDecisions:
    def test_approve_writes_incoming_and_retires_old(self) -> None:
        store = InMemoryGraphStore()
        item, queue = _conflict_item(store, new_tail="Acme", conf=0.99)
        old_id = item.payload["superseded"][0]["id"]
        decided = queue.decide(item.id, ReviewDecision.APPROVE.value, reviewer="op")
        effect = ReviewExecutor(store).apply(decided, ReviewDecision.APPROVE.value)
        assert effect["applied"] is True
        assert effect["retired"] >= 1
        # The incumbent edge is gone; the incoming one is in the graph.
        assert old_id not in _edge_ids(store)
        assert "Acme" in _tail_names(store)
        assert "Zed Corp" not in _tail_names(store)
        assert queue.get(item.id).status == "approved"

    def test_reject_keeps_incumbent(self) -> None:
        store = InMemoryGraphStore()
        item, queue = _conflict_item(store, new_tail="Acme", conf=0.99)
        old_id = item.payload["superseded"][0]["id"]
        decided = queue.decide(item.id, ReviewDecision.REJECT.value, reviewer="op")
        effect = ReviewExecutor(store).apply(decided, ReviewDecision.REJECT.value)
        assert effect["applied"] is False
        assert old_id in _edge_ids(store)

    def test_skip_has_no_effect(self) -> None:
        store = InMemoryGraphStore()
        item, queue = _conflict_item(store)
        before = dict(store._relations)
        decided = queue.decide(item.id, ReviewDecision.SKIP.value, reviewer="op")
        effect = ReviewExecutor(store).apply(decided, ReviewDecision.SKIP.value)
        assert effect["applied"] is False
        assert store._relations == before


class TestExtractionPayloadDecisions:
    def _item_with_triple(self) -> tuple:
        t = _triple("Bob", "Gamma Ltd", 0.8)
        queue = ReviewQueue()
        item = queue.enqueue(
            ReviewType.EXTRACTION,
            {"triple": json.loads(t.model_dump_json())},
            confidence=0.8,
        )
        return item, queue, t

    def test_approve_upserts_triple(self) -> None:
        store = InMemoryGraphStore()
        item, queue, _t = self._item_with_triple()
        decided = queue.decide(item.id, ReviewDecision.APPROVE.value, reviewer="op")
        effect = ReviewExecutor(store).apply(decided, ReviewDecision.APPROVE.value)
        assert effect["applied"] is True
        assert "Gamma Ltd" in _tail_names(store)
        assert queue.get(item.id).status == "approved"

    def test_reject_deletes_triple_from_graph(self) -> None:
        store = InMemoryGraphStore()
        item, queue, t = self._item_with_triple()
        # The triple already made it into the graph through another path.
        _ents, rels = triples_to_records([t])
        store.upsert_entities(_ents)
        store.upsert_relations(rels)
        decided = queue.decide(item.id, ReviewDecision.REJECT.value, reviewer="op")
        effect = ReviewExecutor(store).apply(decided, ReviewDecision.REJECT.value)
        assert effect["applied"] is True
        assert effect["relations"] >= 1
        assert "Gamma Ltd" not in _tail_names(store)

    def test_payload_without_triple_is_reported(self) -> None:
        store = InMemoryGraphStore()
        queue = ReviewQueue()
        item = queue.enqueue(ReviewType.SPOTCHECK, {"note": "no triple here"})
        decided = queue.decide(item.id, ReviewDecision.APPROVE.value, reviewer="op")
        effect = ReviewExecutor(store).apply(decided, ReviewDecision.APPROVE.value)
        assert effect["applied"] is False
        assert "no triple" in effect["reason"]
