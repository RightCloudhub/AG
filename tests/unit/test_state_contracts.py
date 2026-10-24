"""Closed state vocabularies reject invalid transitions and persisted values."""

import pytest

from agentic_graphrag.knowledge.ingest_tasks import IngestTask
from agentic_graphrag.knowledge.review.queue import ReviewQueue, ReviewStatus, ReviewType


def test_invalid_review_decision_does_not_consume_pending_item() -> None:
    queue = ReviewQueue()
    item = queue.enqueue(ReviewType.FEEDBACK, {})
    with pytest.raises(ValueError):
        queue.decide(item.id, "aprove")
    assert item.status == ReviewStatus.PENDING


def test_persisted_ingest_status_is_validated() -> None:
    with pytest.raises(ValueError):
        IngestTask.from_dict({"id": "task", "status": "queded"})


def test_llm_merge_decision_rejects_unknown_action() -> None:
    from agentic_graphrag.knowledge.resolution import MergeDecision

    with pytest.raises(ValueError):
        MergeDecision.model_validate({"action": "merg", "confidence": 0.9})


def test_memory_entity_merge_rewires_edges_and_preserves_other_tenant() -> None:
    from agentic_graphrag.core.ports.stores import EntityRecord, RelationRecord
    from agentic_graphrag.knowledge.resolution_merge import MergeApply, apply_entity_merge
    from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

    store = InMemoryGraphStore()
    keep = EntityRecord(id="keep", name="Canonical", type="Company", tenant_id="t1")
    drop = EntityRecord(id="drop", name="Alias", type="Company", tenant_id="t1")
    other = EntityRecord(id="drop", name="Other", type="Company", tenant_id="t2")
    store.upsert_entities([keep, drop, other])
    edge = RelationRecord(id="edge", type="OWNS", head_id="drop", tail_id="keep", tenant_id="t1")
    foreign = RelationRecord(id="edge", type="OWNS", head_id="drop", tail_id="keep", tenant_id="t2")
    store.upsert_relations([edge, foreign])
    apply_entity_merge(MergeApply(store, keep, drop, "Canonical"))
    assert edge.head_id == keep.id
    assert store.get_entity_by_name(drop.name) is None
    assert store.get_entity_by_name(other.name) == other
    assert foreign.head_id == "drop"
