"""N-01..N-24 regression cases from docs/BL_FIX_VERIFICATION.md §3.3.

Each test is named after its N-id so the checklist can be audited mechanically.
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.routes import knowledge as knowledge_routes
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.api.service_telemetry import save_chain_audit
from agentic_graphrag.generation.citations import (
    claims_lexically_supported,
    validate_answered_claims,
)
from agentic_graphrag.generation.trace import Claim
from agentic_graphrag.knowledge.ingest_tasks import IngestStatus, IngestTaskStore
from agentic_graphrag.knowledge.ingest_worker import IngestWorker, IngestWorkerConfig
from agentic_graphrag.knowledge.review.queue import ReviewQueue, ReviewType
from agentic_graphrag.retrieval.cache import RetrievalCache
from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

# ---------------------------------------------------------------------------
# N-01 — CJK claim lexical support (BL-02)
# ---------------------------------------------------------------------------


class TestN01CjkLexicalSupport:
    def test_cjk_claim_supported_by_cjk_evidence(self) -> None:
        evidence = [
            Candidate(
                id="ev-1",
                source=CandidateSource.GRAPH_NEIGHBOR,
                content="苹果控股的首席执行官是埃琳娜·瓦尔加。",
            )
        ]
        claims = [Claim(text="苹果控股的首席执行官是埃琳娜·瓦尔加。", evidence_ids=["ev-1"])]
        assert claims_lexically_supported(claims, evidence) is True

    def test_cjk_claim_without_overlap_fails(self) -> None:
        evidence = [
            Candidate(
                id="ev-2",
                source=CandidateSource.GRAPH_NEIGHBOR,
                content="完全无关的一段中文证据文本。",
            )
        ]
        claims = [Claim(text="苹果控股的首席执行官是埃琳娜·瓦尔加。", evidence_ids=["ev-2"])]
        assert claims_lexically_supported(claims, evidence) is False


# ---------------------------------------------------------------------------
# N-02 / N-03 — object anchoring (BL-13)
# ---------------------------------------------------------------------------


class TestN02N03ObjectAnchoring:
    """Names are multi-char: the CJK-aware tokenizer drops 1-char latin tokens."""

    def _evidence(self, structured: dict, content: str) -> list[Candidate]:
        return [
            Candidate(
                id="g-1",
                source=CandidateSource.GRAPH_NEIGHBOR,
                content=content,
                structured=structured,
            )
        ]

    def test_n02_neighbor_object_mismatch_rejected(self) -> None:
        evidence = self._evidence(
            {"kind": "neighbor", "query_entity": "Alice", "neighbor": "Xenon", "tail": "Xenon"},
            content="Alice -[FOUNDED_BY]-> Xenon",
        )
        claims = [Claim(text="Alice 的首席执行官是 Ypsilon。", evidence_ids=["g-1"])]
        reason = validate_answered_claims(claims, evidence)
        assert reason == "claim text not supported by cited evidence content"

    def test_n02_neighbor_object_match_passes(self) -> None:
        evidence = self._evidence(
            {"kind": "neighbor", "query_entity": "Alice", "neighbor": "Xenon", "tail": "Xenon"},
            content="Alice -[FOUNDED_BY]-> Xenon",
        )
        claims = [Claim(text="Alice 的首席执行官是 Xenon。", evidence_ids=["g-1"])]
        assert validate_answered_claims(claims, evidence) is None

    def test_n03_path_single_node_hit_rejected(self) -> None:
        evidence = self._evidence(
            {"kind": "path", "nodes": ["Alpha", "Beta"]}, content="Alpha -> Beta path"
        )
        claims = [Claim(text="Alpha 的链路终点是 Gamma。", evidence_ids=["g-1"])]
        assert validate_answered_claims(claims, evidence) is not None

    def test_n03_path_two_node_hits_pass(self) -> None:
        evidence = self._evidence(
            {"kind": "path", "nodes": ["Alpha", "Beta"]}, content="Alpha -> Beta path"
        )
        claims = [Claim(text="Alpha 与 Beta 之间的链路是收购。", evidence_ids=["g-1"])]
        assert validate_answered_claims(claims, evidence) is None


# ---------------------------------------------------------------------------
# N-04 — retrieval cache tenant isolation (BL-04)
# ---------------------------------------------------------------------------


class TestN04CacheTenantIsolation:
    @staticmethod
    def _hit() -> Candidate:
        return Candidate(id="x", source=CandidateSource.VECTOR_CHUNK, content="hit")

    def test_other_tenant_gets_none(self, tmp_path) -> None:
        cache = RetrievalCache(cache_dir=tmp_path / "rc")
        cache.set_retrieval("q", [self._hit()], tenant_id="tenant-a")
        assert cache.get_retrieval("q", tenant_id="tenant-b") is None
        assert cache.get_retrieval("q", tenant_id="tenant-a") is not None

    def test_keys_differ_by_tenant(self, tmp_path) -> None:
        cache = RetrievalCache(cache_dir=tmp_path / "rc")
        assert cache.retrieval_key("q", tenant_id="a") != cache.retrieval_key("q", tenant_id="b")


# ---------------------------------------------------------------------------
# N-05 — streaming initial state carries tenant_id (BL-04)
# ---------------------------------------------------------------------------


class TestN05StreamTenantState:
    def test_stream_initial_state_includes_tenant(self) -> None:
        """Parity with the sync path's initial state (loop_recover inline dict)."""
        import inspect

        from agentic_graphrag.agent import loop_recover, loop_stream
        from agentic_graphrag.generation.trace import ReasoningChain

        chain = ReasoningChain(question="q?", tenant_id="t9", answer="", claims=[], evidence=[])
        stream_state = loop_stream._initial_state("q?", chain, allow_llm=False, tenant_id="t9")
        assert stream_state["tenant_id"] == "t9"
        # The sync path builds its dict inline; its source must carry tenant_id.
        source = inspect.getsource(loop_recover.invoke_agentic_graph)
        assert '"tenant_id": tenant_id' in source


# ---------------------------------------------------------------------------
# N-06 — requeue_stale returns abandoned tasks (BL-06)
# ---------------------------------------------------------------------------


class TestN06RequeueStale:
    def test_stale_extracting_returns_to_queue(self) -> None:
        tasks = IngestTaskStore()
        task = tasks.create("t1", [{"doc_id": "d1"}])
        tasks.transition(task.id, IngestStatus.EXTRACTING)
        requeued = tasks.requeue_stale(stale_seconds=0.0)
        assert task.id in requeued
        assert tasks.get(task.id).status == IngestStatus.QUEUED.value
        assert [t.id for t in tasks.pending()] == [task.id]


# ---------------------------------------------------------------------------
# N-08 / N-09 / N-10 — upload validation (BL-08)
# ---------------------------------------------------------------------------


class TestN08to10UploadValidation:
    @pytest.fixture()
    def client(self):
        svc = QueryService.create_offline()
        app = create_app(query_service=svc)
        with TestClient(app) as c:
            yield c
        svc.close()

    def test_n08_no_extension_rejected(self, client) -> None:
        r = client.post(
            "/v1/docs", files=[("files", ("payload", b"data", "application/octet-stream"))]
        )
        assert r.status_code == 413

    def test_n09_oversized_rejected(self, client) -> None:
        big = b"x" * (5 * 1024 * 1024 + 1)
        r = client.post("/v1/docs", files=[("files", ("big.txt", big, "text/plain"))])
        assert r.status_code == 413

    def test_n10_non_utf8_rejected(self, client) -> None:
        r = client.post(
            "/v1/docs", files=[("files", ("bad.txt", b"\xff\xfe\x00bad", "text/plain"))]
        )
        assert r.status_code == 413


# ---------------------------------------------------------------------------
# N-11 / N-12 / N-13 — cross-tenant decisions and existence probes (BL-09)
# ---------------------------------------------------------------------------


class TestN11to13ReviewAndFeedbackAuth:
    @pytest.fixture()
    def client(self, monkeypatch):
        monkeypatch.setenv("AGR_REQUIRE_AUTH", "1")
        monkeypatch.setenv("AGR_API_KEYS", "alpha:alpha-op:operator,beta:beta-op:operator")
        svc = QueryService.create_offline()
        app = create_app(query_service=svc)
        with TestClient(app) as c:
            yield c, svc
        svc.close()

    def _enqueue_for(self, svc, tenant: str) -> str:
        return svc.review_queue.enqueue(
            ReviewType.CONFLICT, {"note": "x"}, confidence=0.9, tenant_id=tenant
        ).id

    def test_n11_cross_tenant_decision_is_404(self, client) -> None:
        c, svc = client
        item_id = self._enqueue_for(svc, "alpha")
        r = c.post(
            f"/v1/review-queue/{item_id}/decision",
            json={"decision": "approve"},
            headers={"Authorization": "Bearer beta-op"},
        )
        assert r.status_code == 404

    def test_n12_duplicate_decision_is_409(self, client) -> None:
        c, svc = client
        item_id = self._enqueue_for(svc, "alpha")
        h = {"Authorization": "Bearer alpha-op"}
        first = c.post(
            f"/v1/review-queue/{item_id}/decision", json={"decision": "approve"}, headers=h
        )
        assert first.status_code == 200
        second = c.post(
            f"/v1/review-queue/{item_id}/decision", json={"decision": "approve"}, headers=h
        )
        assert second.status_code == 409

    def test_n13_feedback_404_is_uniform(self, client) -> None:
        c, svc = client
        h = {"Authorization": "Bearer beta-op"}
        # A chain that exists but belongs to tenant alpha.
        from agentic_graphrag.generation.trace import ReasoningChain

        chain = ReasoningChain(question="q", tenant_id="alpha", answer="a", claims=[], evidence=[])
        svc.audit_store.save(chain)
        cross = c.post(
            "/v1/feedback",
            json={"query_id": chain.query_id, "accurate": False},
            headers=h,
        )
        missing = c.post(
            "/v1/feedback",
            json={"query_id": str(uuid.uuid4()), "accurate": False},
            headers=h,
        )
        assert cross.status_code == missing.status_code == 404


# ---------------------------------------------------------------------------
# N-14 — /v1/graph/entities tenant filter (BL-09)
# ---------------------------------------------------------------------------


class TestN14GraphEntitiesTenantFilter:
    def test_store_filters_by_tenant(self) -> None:
        from agentic_graphrag.stores.interfaces import EntityRecord

        store = InMemoryGraphStore()
        store.upsert_entities(
            [
                EntityRecord(id="e1", name="Alpha Thing", type="Company", tenant_id="alpha"),
                EntityRecord(id="e2", name="Beta Thing", type="Company", tenant_id="beta"),
            ]
        )
        alpha_names = {e.name for e in store.list_entities(limit=50, tenant_id="alpha")}
        assert alpha_names == {"Alpha Thing"}

    def test_endpoint_reachable(self) -> None:
        svc = QueryService.create_offline()
        app = create_app(query_service=svc)
        with TestClient(app) as c:
            r = c.get("/v1/graph/entities?limit=5")
        svc.close()
        assert r.status_code == 200
        assert isinstance(r.json()["data"], list)


# ---------------------------------------------------------------------------
# N-15 — batch counts conserve with KEEP_OLD (BL-11)
# ---------------------------------------------------------------------------


class TestN15BatchConservation:
    def test_counts_conserve_with_keep_old(self) -> None:
        from agentic_graphrag.knowledge.incremental import IncrementalUpdater
        from agentic_graphrag.knowledge.schema_check import EntityMention, Triple

        store = InMemoryGraphStore()
        seed = Triple(
            head=EntityMention(name="Alice", type="Person"),
            relation="WORKED_AT",
            tail=EntityMention(name="Zed Corp", type="Company"),
            confidence=0.95,
        )
        updater = IncrementalUpdater(store)
        updater.apply_batch([seed])
        incoming = Triple(
            head=EntityMention(name="Alice", type="Person"),
            relation="WORKED_AT",
            tail=EntityMention(name="Acme", type="Company"),
            confidence=0.5,  # lower than incumbent → KEEP_OLD
        )
        result = updater.apply_batch([incoming])
        total = (
            result.accepted
            + result.rejected
            + result.conflicts_auto
            + result.conflicts_review
            + result.conflicts_kept
        )
        assert total == 1
        assert result.conflicts_kept == 1


# ---------------------------------------------------------------------------
# N-16 / N-17 — breadth budget (BL-12)
# ---------------------------------------------------------------------------


class TestN16to17BreadthBudget:
    def test_n16_cap_plan_breadth_records_overflow(self) -> None:
        from agentic_graphrag.agent.plan_dag import cap_plan_breadth

        sqs = [object() for _ in range(8)]
        kept, dropped = cap_plan_breadth(sqs, 6)
        assert len(kept) == 6
        assert len(dropped) == 2

    def test_n17_guardrail_reason_distinguishes_breadth_and_depth(self) -> None:
        from agentic_graphrag.agent.guardrails import GuardrailConfig, Guardrails

        cfg = GuardrailConfig(max_hops=4)
        g = Guardrails(cfg)
        g.note_plan_breadth(6)
        for _ in range(5):
            g.on_hop_start()
        assert "breadth stop" in g.state.reason

        g2 = Guardrails(GuardrailConfig(max_hops=4))
        g2.note_plan_breadth(3)
        for _ in range(5):
            g2.on_hop_start()
        assert "depth stop" in g2.state.reason


# ---------------------------------------------------------------------------
# N-18 — memory graph delete_relation (C-07 / BL-11)
# ---------------------------------------------------------------------------


class TestN18DeleteRelation:
    def test_delete_removes_exactly_one_edge(self) -> None:
        from agentic_graphrag.knowledge.graph_builder import triples_to_records
        from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
        from agentic_graphrag.stores.interfaces import RelationRecord  # noqa: F401

        store = InMemoryGraphStore()
        t = Triple(
            head=EntityMention(name="A", type="Person"),
            relation="WORKED_AT",
            tail=EntityMention(name="B", type="Company"),
            confidence=0.9,
        )
        _ents, rels = triples_to_records([t])
        store.upsert_entities(_ents)
        store.upsert_relations(rels)
        before = store.counts()["relationships"]
        assert store.delete_relation(rels[0].id) is True
        assert store.counts()["relationships"] == before - 1


# ---------------------------------------------------------------------------
# N-19 — audit failure is visible (BL-10)
# ---------------------------------------------------------------------------


class TestN19AuditFailureVisible:
    def test_save_failure_returns_false(self, caplog) -> None:
        from agentic_graphrag.generation.trace import ReasoningChain

        class ExplodingStore:
            def save(self, chain) -> None:
                raise RuntimeError("disk full")

        chain = ReasoningChain(question="q", tenant_id="t", answer="a", claims=[], evidence=[])
        with caplog.at_level("ERROR"):
            ok = save_chain_audit(ExplodingStore(), chain)
        assert ok is False
        assert any("Audit save failed" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# N-20 — all-embed failure fails the task (BL-10)
# ---------------------------------------------------------------------------


class TestN20EmbedFailure:
    def test_all_embed_failure_marks_error(self) -> None:
        from agentic_graphrag.stores.interfaces import DocumentRecord

        def boom(_text: str) -> list[float]:
            raise RuntimeError("no embedder")

        class DocStore:
            durable = True

            def get(self, doc_id: str) -> DocumentRecord | None:
                return DocumentRecord(doc_id=doc_id, title="t", content="hello world text")

            def list_ids(self, *, tenant_id: str | None = None) -> list[str]:
                return []

        class Vector:
            def upsert(self, chunks) -> int:
                return len(chunks)

        worker = IngestWorker(
            doc_store=DocStore(),
            vector_store=Vector(),
            embed_fn=boom,
            config=IngestWorkerConfig(),
        )
        result = worker.process_doc("doc-embed")
        assert result.error is not None
        assert result.error.startswith("embed_failed_all")


# ---------------------------------------------------------------------------
# N-21 — corrupt review-queue line is skipped (BL-11)
# ---------------------------------------------------------------------------


class TestN21CorruptReviewLine:
    def test_single_bad_line_does_not_break_queue(self, tmp_path) -> None:
        path = tmp_path / "review_queue.jsonl"
        good = {
            "id": "item-1",
            "type": "conflict",
            "payload": {"note": "x"},
            "status": "pending",
            "confidence": 0.5,
        }
        path.write_text("{bad json\n" + json.dumps(good) + "\n", encoding="utf-8")
        queue = ReviewQueue(path)
        assert queue.get("item-1") is not None
        assert queue.counts()["total"] == 1


# ---------------------------------------------------------------------------
# N-22 — _TASKS fallback cache is bounded (BL-11)
# ---------------------------------------------------------------------------


class TestN22TaskCacheBounded:
    def test_600_tasks_leave_500(self, monkeypatch) -> None:
        monkeypatch.setattr(knowledge_routes, "_TASKS", knowledge_routes.OrderedDict())
        for i in range(600):
            knowledge_routes._remember_task(f"t-{i}", {"id": f"t-{i}"})
        assert len(knowledge_routes._TASKS) == knowledge_routes.MAX_CACHED_TASKS
        assert "t-0" not in knowledge_routes._TASKS
        assert "t-599" in knowledge_routes._TASKS


# ---------------------------------------------------------------------------
# N-23 — streamed audit chain carries request_id (BL-11)
# ---------------------------------------------------------------------------


class TestN23StreamRequestId:
    def test_stream_chain_has_request_id(self, tmp_path, monkeypatch) -> None:
        from agentic_graphrag.api.schemas import QueryRequest
        from agentic_graphrag.observability.logging_setup import request_id_var

        svc = QueryService.create_offline()

        class CapturingStore:
            def __init__(self) -> None:
                self.chains: list = []

            def save(self, chain) -> None:
                self.chains.append(chain)

            def get_for_tenant(self, query_id, tenant_id):
                return None

        captured = CapturingStore()
        svc.audit_store = captured
        request_id_var.set("req-abc-123")
        try:
            req = QueryRequest(question="Who is the CEO of Apex Holdings?")
            list(svc.stream_query_events(req, tenant_id="t", user_id="u"))
        finally:
            request_id_var.set("")
        assert captured.chains, "expected the streamed chain to be audited"
        meta = captured.chains[-1].metadata or {}
        assert meta.get("request_id") == "req-abc-123"


# ---------------------------------------------------------------------------
# N-24 — unbound_claim_rate tolerates legacy rows (C-01)
# ---------------------------------------------------------------------------


class TestN24UnboundClaimRateCompat:
    def test_legacy_row_without_catalog_is_not_fabrication(self) -> None:
        from agentic_graphrag.eval.metrics_evidence import _claims_fail_gate

        legacy_claim = {"text": "Alice worked at Acme", "evidence_ids": ["ev-1"]}
        # No catalog (older reports / baseline rows): only id presence is
        # checkable, so the row must not be accused of fabricating.
        assert _claims_fail_gate([legacy_claim], {}) is False

    def test_bound_row_with_catalog_passes(self) -> None:
        from agentic_graphrag.eval.metrics_evidence import _CatalogEntry, _claims_fail_gate

        claim = {"text": "Alice worked at Acme", "evidence_ids": ["ev-1"]}
        catalog = {"ev-1": _CatalogEntry(content="Alice worked at Acme.", truncated=False)}
        assert _claims_fail_gate([claim], catalog) is False
