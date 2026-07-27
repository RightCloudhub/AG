"""Regression tests for logic issues found in code review (formerly characterization)."""

from __future__ import annotations

import math

from agentic_graphrag.api.service_query import MS_PER_SECOND
from agentic_graphrag.eval.scoring import score_pair
from agentic_graphrag.generation.citations import _content_tokens, claims_lexically_supported
from agentic_graphrag.generation.trace import Claim
from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource


def _timeout_seconds(timeout_ms: int) -> int:
    """Mirror service_query conversion (ceil, min 1)."""
    return max(1, math.ceil(timeout_ms / MS_PER_SECOND))


def test_score_pair_no_inside_know_is_not_correct() -> None:
    """B8 fix: gold 'no' must not match inside 'know'."""
    row = score_pair("I know nothing about it", "no")
    assert row["correct"] is False


def test_score_pair_yes_inside_yesterday_is_not_correct() -> None:
    row = score_pair("yesterday's report", "yes")
    assert row["correct"] is False


def test_score_pair_explicit_yes_no_still_works() -> None:
    assert score_pair("the answer is no", "no")["correct"] is True
    assert score_pair("yes", "yes")["correct"] is True


def test_timeout_ms_subsecond_not_disabled() -> None:
    """B4 fix: sub-1s timeouts become 1s, never 0 (which disabled wall-clock)."""
    for ms in (100, 500, 999):
        assert _timeout_seconds(ms) == 1
    assert _timeout_seconds(1000) == 1
    assert _timeout_seconds(1500) == 2


# --- BL-02: CJK-aware tokenization for citation lexical support ---


def test_content_tokens_cjk_yields_unigrams_and_bigrams() -> None:
    """BL-02 fix: CJK text must split into unigrams + bigrams, not one blob."""
    toks = _content_tokens("苹果控股的首席执行官")
    # Unigrams
    assert "苹" in toks and "果" in toks and "控" in toks
    # Bigrams
    assert "苹果" in toks and "果控" in toks and "控股" in toks


def test_content_tokens_latin_preserves_prior_behaviour() -> None:
    """BL-02 fix must not regress Latin tokenization."""
    toks = _content_tokens("Elena Varga is the CEO of Apex Holdings")
    assert "elena" in toks
    assert "varga" in toks
    assert "apex" in toks
    assert "holdings" in toks
    # Stopwords filtered
    assert "the" not in toks
    assert "is" not in toks


def test_claims_lexically_supported_pure_cjk_now_passes() -> None:
    """BL-02 fix: pure-CJK claim + CJK evidence must be able to pass (previously always failed)."""
    evidence = [
        Candidate(
            id="e1",
            source=CandidateSource.GRAPH_NEIGHBOR,
            content="苹果控股的首席执行官是埃琳娜·瓦尔加",
            score=1.0,
        )
    ]
    claims = [Claim(text="苹果控股的首席执行官是埃琳娜·瓦尔加", evidence_ids=["e1"])]
    assert claims_lexically_supported(claims, evidence) is True


def test_claims_lexically_supported_cjk_no_overlap_still_fails() -> None:
    """BL-02 fix must not over-loosen: disjoint CJK claim + evidence still fails."""
    evidence = [
        Candidate(
            id="e1",
            source=CandidateSource.GRAPH_NEIGHBOR,
            content="NovaTech创立于二零一零年",
            score=1.0,
        )
    ]
    claims = [Claim(text="马库斯·韦伯担任首席执行官", evidence_ids=["e1"])]
    # No shared CJK unigram/bigram — must fail.
    assert claims_lexically_supported(claims, evidence) is False


def test_claims_lexically_supported_mixed_cjk_latin() -> None:
    """BL-02 fix: mixed CJK + Latin claim can match via either script."""
    evidence = [
        Candidate(
            id="e1",
            source=CandidateSource.GRAPH_NEIGHBOR,
            content="Elena Varga -[CEO_OF]-> Apex Holdings",
            score=1.0,
        )
    ]
    # Latin overlap on "elena"/"varga"/"apex"/"holdings".
    claims = [Claim(text="Elena Varga是Apex Holdings的首席执行官", evidence_ids=["e1"])]
    assert claims_lexically_supported(claims, evidence) is True


# --- BL-04: SSE streaming path tenant_id propagation ---


def test_stream_initial_state_includes_tenant_id() -> None:
    """BL-04 fix: streaming _initial_state must carry tenant_id (was missing → cross-tenant leak)."""
    from agentic_graphrag.agent.loop_stream import _initial_state
    from agentic_graphrag.generation.trace import ReasoningChain

    chain = ReasoningChain(question="q")
    state = _initial_state("q", chain, allow_llm=True, tenant_id="tenant-A")
    assert state.get("tenant_id") == "tenant-A"

    # Default empty string preserves prior call-site compatibility.
    state_default = _initial_state("q", chain, allow_llm=True)
    assert "tenant_id" in state_default
    assert state_default["tenant_id"] == ""


# --- BL-03: Review decision executor actually applies to graph ---


def test_review_executor_approve_writes_triple_to_graph() -> None:
    """BL-03 fix: APPROVE on a CONFLICT item must write the incoming triple to the graph."""
    from agentic_graphrag.knowledge.review.queue import (
        ReviewDecision,
        ReviewExecutor,
        ReviewQueue,
        ReviewType,
    )
    from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
    from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

    store = InMemoryGraphStore()
    queue = ReviewQueue()
    executor = ReviewExecutor(store)
    # Enqueue a conflict with an incoming triple
    triple = Triple(
        head=EntityMention(name="Elena", type="Person"),
        relation="CEO_OF",
        tail=EntityMention(name="Acme", type="Company"),
        confidence=0.9,
    )
    item = queue.enqueue(
        ReviewType.CONFLICT,
        {"incoming": triple.model_dump(mode="json"), "existing": None},
        confidence=0.9,
    )
    # Before: graph is empty
    assert store.counts().get("relationships", 0) == 0
    # Approve
    decided = queue.decide(item.id, ReviewDecision.APPROVE, reviewer="admin")
    outcome = executor.apply_decision(decided)
    assert outcome["action"] == "approve"
    assert outcome["relations_upserted"] >= 1
    # After: graph has the relation
    assert store.counts().get("relationships", 0) >= 1


def test_review_executor_reject_removes_relation_from_graph() -> None:
    """BL-03 fix: REJECT on a CONFLICT item must remove the existing relation from the graph."""
    from agentic_graphrag.knowledge.review.queue import (
        ReviewDecision,
        ReviewExecutor,
        ReviewQueue,
        ReviewType,
    )
    from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
    from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

    store = InMemoryGraphStore()
    # Seed the graph with a relation
    from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph

    seed = Triple(
        head=EntityMention(name="Old CEO", type="Person"),
        relation="CEO_OF",
        tail=EntityMention(name="Acme", type="Company"),
        confidence=0.5,
    )
    load_triples_into_graph(store, [seed], clear_first=True)
    rels_before = store.counts().get("relationships", 0)
    assert rels_before >= 1
    # Find the relation id
    rel_list = list(store._relations.values())  # type: ignore[attr-defined]
    assert rel_list
    rel_id = rel_list[0].id
    # Enqueue a conflict referencing the existing relation
    queue = ReviewQueue()
    executor = ReviewExecutor(store)
    item = queue.enqueue(
        ReviewType.CONFLICT,
        {
            "existing": {
                "id": rel_id,
                "type": "CEO_OF",
                "head": "Old CEO",
                "tail": "Acme",
                "confidence": 0.5,
            },
            "incoming": None,
        },
        confidence=0.5,
    )
    # Reject
    decided = queue.decide(item.id, ReviewDecision.REJECT, reviewer="admin")
    outcome = executor.apply_decision(decided)
    assert outcome["action"] == "reject"
    assert outcome["removed"] == rel_id
    # After: graph has one fewer relation
    rels_after = store.counts().get("relationships", 0)
    assert rels_after == rels_before - 1


def test_incremental_updater_enqueues_conflicts_to_review_queue() -> None:
    """BL-03 fix: IncrementalUpdater with review_queue must enqueue conflicts (not just JSONL)."""
    from agentic_graphrag.knowledge.incremental import IncrementalUpdater
    from agentic_graphrag.knowledge.review.queue import ReviewQueue, ReviewStatus, ReviewType
    from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
    from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

    store = InMemoryGraphStore()
    queue = ReviewQueue()
    # Seed with a low-confidence edge
    seed = Triple(
        head=EntityMention(name="A", type="Company"),
        relation="PARENT_OF",
        tail=EntityMention(name="B", type="Company"),
        confidence=0.5,
    )
    updater = IncrementalUpdater(store, confidence_threshold=0.3, review_queue=queue)
    updater.apply_batch([seed])
    assert queue.counts().get("total", 0) == 0  # no conflicts yet
    # Submit a conflicting edge with slightly higher confidence → REVIEW
    conflict = Triple(
        head=EntityMention(name="A", type="Company"),
        relation="PARENT_OF",
        tail=EntityMention(name="C", type="Company"),
        confidence=0.6,
    )
    updater.apply_batch([conflict])
    # The conflict should be enqueued in the review queue
    items = queue.list(status=None)
    assert len(items) >= 1
    assert any(i.type == ReviewType.CONFLICT.value for i in items)
