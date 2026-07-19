"""Extra unit coverage for P3/P4 modules."""

from __future__ import annotations

from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.fast_path import run_fast_path
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import run_query
from agentic_graphrag.agent.tools.registry import (
    ExternalToolSpec,
    default_retrieval_tools,
)
from agentic_graphrag.api.auth import (
    RateLimiter,
    parse_api_keys,
)
from agentic_graphrag.api.sse import EVENT_ANSWER, iter_query_events
from agentic_graphrag.generation.audit_store import AuditStore
from agentic_graphrag.generation.confidence import grade_confidence
from agentic_graphrag.generation.trace import QueryStatus, ReasoningChain
from agentic_graphrag.knowledge.resolution import EntityResolver, char_ngram_sim, normalize_name
from agentic_graphrag.knowledge.review.queue import ReviewQueue, ReviewType
from agentic_graphrag.llm.budget_policy import BudgetLimits, MultiLevelBudget
from agentic_graphrag.observability.metrics import MetricsRegistry, QueryMetrics
from agentic_graphrag.observability.trace import get_tracer, span
from agentic_graphrag.retrieval.cache import MemoryCache, content_hash, normalize_query_key
from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource
from agentic_graphrag.retrieval.fulltext import FulltextRetriever
from agentic_graphrag.retrieval.fusion import IdentityReranker, fuse_candidates
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.interfaces import ChunkRecord, EntityRecord, RelationRecord
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore


def test_memory_cache_expire_and_prefix():
    c = MemoryCache(max_entries=2)
    c.set("a", 1, ttl_seconds=0.0)
    # ttl 0 means expires immediately on next get if created_at is past
    c._data["a"].created_at = 0
    assert c.get("a") is None
    c.set("p:1", 1)
    c.set("p:2", 2)
    c.set("q:3", 3)  # may drop oldest
    assert c.invalidate_prefix("p:") >= 0
    c.clear()
    assert c.stats()["size"] == 0


def test_content_hash_stable():
    assert content_hash("x") == content_hash("x")
    assert normalize_query_key("  Hello   World ") == "hello world"


def test_fusion_concat_and_reranker():
    a = [Candidate(id="1", source=CandidateSource.VECTOR_CHUNK, content="a", score=1)]
    b = [Candidate(id="2", source=CandidateSource.FULLTEXT_CHUNK, content="b", score=1)]
    out = fuse_candidates(a, b, method="concat")
    assert len(out) == 2
    out2 = fuse_candidates(a, b, method="rrf", reranker=IdentityReranker(), limit=1)
    assert len(out2) == 1


def test_tool_registry():
    reg = default_retrieval_tools()
    assert reg.get("vector_search") is not None
    assert "vector_search" in reg.describe_for_llm()
    called = {}

    def h(x: int = 0) -> int:
        called["x"] = x
        return x + 1

    reg.register(
        ExternalToolSpec(name="echo", description="e", handler=h, permissions=["x"])
    )
    assert reg.invoke("echo", x=2) == 3


def test_metrics_and_trace():
    reg = MetricsRegistry()
    reg.record(
        QueryMetrics(query_id="q1", route="fast_path", hops=1, latency_ms=10, status="answered")
    )
    reg.record_budget_trip()
    s = reg.summary()
    assert s["count"] == 1
    assert s["budget_trips"] >= 1
    assert reg.percentile(50) >= 0
    assert len(reg.recent(10)) == 1

    tr = get_tracer()
    ctx = tr.start(query_id="qid-1", tenant_id="t")
    with span(ctx, "work", hop=1) as sp:
        sp.attributes["ok"] = True
    ctx.add_event("done")
    assert tr.get("qid-1") is not None
    assert len(ctx.to_dict()["spans"]) >= 1


def test_resolution_dry_run_merge():
    ents = [
        EntityRecord(id="a", name="Helix Systems", type="Company"),
        EntityRecord(id="b", name="Helix System", type="Company"),
    ]
    r = EntityResolver(similarity_threshold=0.5, auto_merge_threshold=0.99)
    # high exact-ish
    store = InMemoryGraphStore()
    store.upsert_entities(ents)
    result = r.resolve(store, ents, allow_llm=False, dry_run=True)
    assert isinstance(result.to_dict(), dict)
    assert char_ngram_sim("abc", "abc") == 1.0
    assert normalize_name("  x  ") == "x"


def test_review_filters():
    q = ReviewQueue()
    q.enqueue(ReviewType.CONFLICT, {"a": 1}, confidence=0.2)
    q.enqueue(ReviewType.EXTRACTION, {"b": 2}, confidence=0.9)
    items = q.list(status="pending", type="extraction", min_confidence=0.5, limit=10)
    assert len(items) == 1
    assert q.get(items[0].id) is not None


def test_budget_tenant_trip():
    mb = MultiLevelBudget(
        tenant_limits=BudgetLimits(max_llm_calls=1, max_tokens=100, max_cost_units=1),
        user_limits=BudgetLimits(max_llm_calls=100, max_tokens=10000, max_cost_units=100),
    )
    mb.commit(tenant_id="t", user_id="u", llm_calls=1, tokens=1, cost_units=0.5)
    try:
        mb.check_and_reserve(tenant_id="t", user_id="u", estimated_calls=1)
        ok = True
    except Exception:
        ok = False
    assert ok is False
    snap = mb.snapshot()
    assert "tenants" in snap


def test_sse_iter():
    frames = list(iter_query_events([(EVENT_ANSWER, {"a": 1})]))
    assert "event: answer" in frames[0]


def test_auth_rate_limiter():
    lim = RateLimiter(qps=2, concurrent=5, window_seconds=60)
    assert lim.acquire("t") is None
    assert lim.acquire("t") is None
    # third hit exceeds qps=2
    err = lim.acquire("t")
    assert err is not None and "QPS" in err
    lim.release("t")
    lim.release("t")
    # concurrent cap
    lim2 = RateLimiter(qps=100, concurrent=1, window_seconds=60)
    assert lim2.acquire("u") is None
    assert lim2.acquire("u") is not None
    lim2.release("u")
    keys = parse_api_keys("solo-key")
    assert keys["solo-key"] == "default"


def test_fast_path_and_run_query_offline():
    store = InMemoryGraphStore()
    store.upsert_entities(
        [EntityRecord(id="1", name="Apex Holdings", type="Company")]
    )
    store.upsert_relations(
        [
            RelationRecord(
                id="r1",
                type="CEO_OF",
                head_id="2",
                tail_id="1",
                head_name="Elena Varga",
                tail_name="Apex Holdings",
            )
        ]
    )
    store.upsert_entities([EntityRecord(id="2", name="Elena Varga", type="Person")])
    graph = GraphRetriever(store)
    ft = BM25FulltextStore()
    ft.index(
        [
            ChunkRecord(
                chunk_id="c1",
                doc_id="d1",
                text="Elena Varga is the CEO of Apex Holdings.",
                index=0,
            )
        ]
    )
    ex = Executor(
        graph=graph,
        fulltext=FulltextRetriever(ft),
        known_entities=["Apex Holdings", "Elena Varga"],
        parallel=True,
        fusion_method="rrf",
    )
    chain = run_fast_path("Who is the CEO of Apex Holdings?", ex, None, allow_llm=False)
    assert chain.route == "fast_path"
    assert chain.answer

    chain2 = run_query(
        "Who is the CEO of Apex Holdings?",
        ex,
        None,
        guard_cfg=GuardrailConfig(max_hops=3),
        allow_llm=False,
        enable_triage=True,
    )
    assert chain2.route in {"fast_path", "agentic"}
    assert chain2.answer

    chain3 = run_query(
        "Who is the CEO of the parent company of BrightLink Logistics?",
        ex,
        None,
        allow_llm=False,
        force_agentic=True,
    )
    assert chain3.metadata.get("force_agentic") is True


def test_confidence_no_answer():
    chain = ReasoningChain(question="q", status=QueryStatus.NO_ANSWER)
    g = grade_confidence(chain, [])
    assert g["level"] == "none"


def test_audit_missing_query_id_raises(tmp_path):
    store = AuditStore(tmp_path / "a.jsonl")
    try:
        store.save({"answer": "x"})
        raised = False
    except ValueError:
        raised = True
    assert raised
    assert store.list_ids() == []
