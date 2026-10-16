"""Enterprise completion regression tests (ENT-05/06/08)."""

from __future__ import annotations

import json
import time

import pytest

from agentic_graphrag.api.app import _validate_live_credentials
from agentic_graphrag.api.auth import RateLimiter
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.knowledge.ingest import chunk_document
from agentic_graphrag.knowledge.ingest_tasks import IngestStatus, IngestTaskStore
from agentic_graphrag.knowledge.ingest_worker import IngestWorker
from agentic_graphrag.stores.doc_store import FileDocStore, InMemoryDocStore
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.interfaces import (
    ChunkRecord,
    DocumentRecord,
    EntityRecord,
    RelationRecord,
)
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore


def _chunk(chunk_id: str, tenant_id: str, score: float, text: str = "shared term") -> ChunkRecord:
    return ChunkRecord(
        chunk_id=chunk_id,
        doc_id=chunk_id,
        text=text,
        index=0,
        embedding=[score, 1.0 - score],
        tenant_id=tenant_id,
    )


def test_vector_filters_tenant_before_top_k() -> None:
    store = InMemoryVectorStore()
    store.upsert([_chunk("other", "beta", 1.0), _chunk("mine", "acme", 0.8)])
    hits = store.search([1.0, 0.0], top_k=1, tenant_id="acme")
    assert [chunk.chunk_id for chunk, _ in hits] == ["mine"]


def test_fulltext_filters_tenant_before_top_k() -> None:
    store = BM25FulltextStore()
    store.index(
        [
            _chunk("other", "beta", 1.0, "term term term"),
            _chunk("mine", "acme", 1.0, "term"),
        ]
    )
    hits = store.search("term", top_k=1, tenant_id="acme")
    assert [chunk.chunk_id for chunk, _ in hits] == ["mine"]


def test_graph_explicit_tenants_are_isolated() -> None:
    store = InMemoryGraphStore()
    entities = [
        EntityRecord("a1", "A", "Org", tenant_id="acme"),
        EntityRecord("b1", "B", "Org", tenant_id="acme"),
        EntityRecord("a2", "A", "Org", tenant_id="beta"),
        EntityRecord("c2", "C", "Org", tenant_id="beta"),
    ]
    relations = [
        RelationRecord("r1", "LINK", "a1", "b1", "A", "B", tenant_id="acme"),
        RelationRecord("r2", "LINK", "a2", "c2", "A", "C", tenant_id="beta"),
    ]
    store.upsert_entities(entities)
    store.upsert_relations(relations)
    acme = store.neighbors("A", tenant_id="acme")
    assert [entity.name for _, entity in acme] == ["B"]
    assert store.paths("A", "C", tenant_id="acme") == []


def test_document_and_chunk_tenant_roundtrip(tmp_path) -> None:
    doc = DocumentRecord("doc", "Title", "text", tenant_id="acme")
    store = FileDocStore(tmp_path)
    store.save(doc)
    loaded = store.get("doc")
    assert loaded is not None and loaded.tenant_id == "acme"
    assert store.list_ids(tenant_id="beta") == []
    assert chunk_document(loaded)[0].tenant_id == "acme"


def test_rate_limiter_uses_tenant_override() -> None:
    limiter = RateLimiter(qps=10, concurrent=10, tenant_overrides={"acme": (1.0, 1)})
    assert limiter.acquire("acme") is None
    assert limiter.acquire("acme") is not None
    limiter.release("acme")


def test_live_credentials_fail_fast(monkeypatch) -> None:
    svc = QueryService.create_offline()
    monkeypatch.setenv("AGR_USE_LIVE_STORES", "1")
    svc.settings.neo4j_password = ""
    try:
        with pytest.raises(RuntimeError, match="NEO4J_PASSWORD"):
            _validate_live_credentials(svc)
    finally:
        svc.close()


def test_ingest_task_store_persists_transitions(tmp_path) -> None:
    path = tmp_path / "tasks.jsonl"
    store = IngestTaskStore(path)
    task = store.create("acme", [{"doc_id": "d1"}])
    store.transition(task.id, IngestStatus.EXTRACTING)
    store.transition(task.id, IngestStatus.DONE)
    reloaded = IngestTaskStore(path).get(task.id, tenant_id="acme")
    assert reloaded is not None and reloaded.status == "done"
    assert IngestTaskStore(path).get(task.id, tenant_id="beta") is None


def test_ingest_worker_advances_task_to_done(tmp_path) -> None:
    tasks = IngestTaskStore(tmp_path / "tasks.jsonl")
    docs = InMemoryDocStore()
    docs.save(DocumentRecord("d1", "Title", "hello world", tenant_id="acme"))
    task = tasks.create("acme", [{"doc_id": "d1"}])
    vector = InMemoryVectorStore()
    worker = IngestWorker(
        doc_store=docs,
        vector_store=vector,
        fulltext_store=BM25FulltextStore(),
        embed_fn=lambda _text: [1.0, 0.0],
        task_store=tasks,
    )
    results = worker.run_once()
    assert results[0].chunks_indexed == 1
    assert tasks.get(task.id).status == IngestStatus.DONE


def test_pruner_removes_only_expired_rows(tmp_path) -> None:
    from agentic_graphrag.retention import PruneTarget, prune_jsonl

    path = tmp_path / "events.jsonl"
    now = time.time()
    rows = [{"ts": now - 200_000}, {"ts": now}, {"no_ts": True}]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = prune_jsonl(PruneTarget(path, 1, ("ts",)), now=now)
    assert result.removed == 1
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
