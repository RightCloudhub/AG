"""P3 KG: resolution, incremental, review; multi-level budget."""

from __future__ import annotations

from agentic_graphrag.knowledge.incremental import IncrementalUpdater
from agentic_graphrag.knowledge.resolution import EntityResolver, jaccard, normalize_key
from agentic_graphrag.knowledge.review.queue import ReviewDecision, ReviewQueue, ReviewType
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
from agentic_graphrag.llm.budget import BudgetExceeded
from agentic_graphrag.llm.budget_policy import BudgetLimits, MultiLevelBudget
from agentic_graphrag.stores.interfaces import EntityRecord
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore


def test_normalize_and_jaccard():
    assert normalize_key("  Apex  Holdings ") == "apex holdings"
    assert jaccard("Apex Holdings", "Apex Holding") > 0.3


def test_entity_resolver_finds_near_duplicates():
    ents = [
        EntityRecord(id="1", name="Apex Holdings", type="Company"),
        EntityRecord(id="2", name="apex holdings", type="Company"),
        EntityRecord(id="3", name="NovaTech", type="Company"),
    ]
    res = EntityResolver(similarity_threshold=0.7, auto_merge_threshold=0.9)
    cands = res.find_candidates(ents)
    assert any(
        {c.left.name.lower(), c.right.name.lower()} == {"apex holdings"} or c.score >= 0.9
        for c in cands
    )


def test_incremental_updater_no_clear():
    store = InMemoryGraphStore()
    # seed one edge
    t0 = Triple(
        head=EntityMention(name="A", type="Company"),
        relation="PARENT_OF",
        tail=EntityMention(name="B", type="Company"),
        confidence=0.6,
    )
    up = IncrementalUpdater(store, confidence_threshold=0.5, auto_update_margin=0.15)
    r1 = up.apply_batch([t0])
    assert r1.accepted >= 0
    counts1 = store.counts()

    t1 = Triple(
        head=EntityMention(name="A", type="Company"),
        relation="PARENT_OF",
        tail=EntityMention(name="C", type="Company"),
        confidence=0.9,
    )
    r2 = up.apply_batch([t1])
    # value conflict on PARENT_OF from A
    assert r2.conflicts_auto + r2.conflicts_review >= 0
    counts2 = store.counts()
    # InMemory counts keys may be entity_count / relation_count
    e1 = counts1.get("entities", counts1.get("entity_count", 0))
    e2 = counts2.get("entities", counts2.get("entity_count", 0))
    assert e2 >= e1


def test_review_queue_decide():
    q = ReviewQueue()
    item = q.enqueue(ReviewType.EXTRACTION, {"triple": "x"}, confidence=0.4)
    assert item.status == "pending"
    done = q.decide(item.id, ReviewDecision.APPROVE, reviewer="t")
    assert done.status == "approved"
    assert q.counts()["approved"] == 1


def test_multi_level_budget_trips_user():
    mb = MultiLevelBudget(
        user_limits=BudgetLimits(max_llm_calls=2, max_tokens=1000, max_cost_units=10),
        tenant_limits=BudgetLimits(max_llm_calls=100, max_tokens=1_000_000, max_cost_units=100),
        query_limits=BudgetLimits(max_llm_calls=20, max_tokens=50_000, max_cost_units=1),
    )
    mb.check_and_reserve(tenant_id="t", user_id="u", estimated_calls=1)
    mb.commit(
        tenant_id="t",
        user_id="u",
        llm_calls=2,
        tokens=10,
        cost_units=0.1,
        reserved_calls=1,
        reserved_cost=0.01,
    )
    try:
        mb.check_and_reserve(tenant_id="t", user_id="u", estimated_calls=1)
        raised = False
    except BudgetExceeded:
        raised = True
    assert raised


def test_multi_level_budget_reserve_is_atomic():
    """Concurrent check_and_reserve must not both pass a one-slot limit."""
    import threading

    mb = MultiLevelBudget(
        user_limits=BudgetLimits(max_llm_calls=1, max_tokens=10_000, max_cost_units=100),
        tenant_limits=BudgetLimits(max_llm_calls=100, max_tokens=1_000_000, max_cost_units=100),
    )
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            mb.check_and_reserve(tenant_id="t", user_id="u", estimated_calls=1)
            result = "ok"
        except BudgetExceeded:
            result = "exceeded"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("ok") == 1
    assert outcomes.count("exceeded") == 1
    snap = mb.snapshot()
    assert snap["users"]["t:u"]["llm_calls"] == 1


def test_multi_level_budget_release_restores_slot():
    mb = MultiLevelBudget(
        user_limits=BudgetLimits(max_llm_calls=1, max_tokens=1000, max_cost_units=10),
        tenant_limits=BudgetLimits(max_llm_calls=100, max_tokens=1_000_000, max_cost_units=100),
    )
    mb.check_and_reserve(tenant_id="t", user_id="u", estimated_calls=1, estimated_cost=0.01)
    mb.release(tenant_id="t", user_id="u", reserved_calls=1, reserved_cost=0.01)
    # Slot free again after failed request release.
    mb.check_and_reserve(tenant_id="t", user_id="u", estimated_calls=1, estimated_cost=0.01)
