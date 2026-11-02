"""Durable document lifecycle and indexing regressions; no network services."""

import uuid

import httpx
import pytest

from agentic_graphrag.core.ports.stores import DocumentRecord, RelationRecord
from agentic_graphrag.knowledge.document_lifecycle import DocumentLifecycle
from agentic_graphrag.knowledge.ingest_tasks import IngestStatus, IngestTaskStore
from agentic_graphrag.knowledge.ingest_worker import IngestWorker, IngestWorkerConfig
from agentic_graphrag.stores.doc_store import FileDocStore
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore


def setup_pipeline(tmp_path, *, embed=None, fulltext=None):
    docs, vectors, graph = FileDocStore(tmp_path), InMemoryVectorStore(), InMemoryGraphStore()
    ft = fulltext or BM25FulltextStore()
    lifecycle = DocumentLifecycle(docs, vector=vectors, fulltext=ft, graph=graph)
    tasks = IngestTaskStore(tmp_path / "tasks.jsonl")
    worker = IngestWorker(
        doc_store=docs,
        vector_store=vectors,
        fulltext_store=ft,
        embed_many=embed or (lambda texts: [[1.0, 0.0] for _ in texts]),
        task_store=tasks,
        config=IngestWorkerConfig(
            chunk_size_chars=30, chunk_overlap_chars=0, embedding_batch_size=2, retry_base_seconds=0
        ),
    )
    return lifecycle, worker, tasks, graph


def upload(lifecycle, content="evidence " * 12, *, tenant="alpha", source="upload://a.txt"):
    return lifecycle.accept(
        DocumentRecord("", "a.txt", content, tenant_id=tenant, source_uri=source)
    )


def test_reingest_fewer_chunks_and_delete(tmp_path):
    lifecycle, worker, tasks, graph = setup_pipeline(tmp_path)
    first = upload(lifecycle)
    uuid.UUID(first["doc_id"])
    tasks.create("alpha", [first])
    worker.run_once()
    did = first["doc_id"]
    graph.upsert_relations(
        [
            RelationRecord("only", "LINK", "a", "b", sources=[{"doc_id": did}], tenant_id="alpha"),
            RelationRecord(
                "shared",
                "LINK",
                "b",
                "c",
                tenant_id="alpha",
                sources=[{"doc_id": did}, {"doc_id": "other"}],
            ),
        ]
    )
    second = upload(lifecycle, "new evidence")
    assert second["doc_id"] == did and second["version"] == "2"
    tasks.create("alpha", [second])
    worker.run_once()
    assert len(worker.vector_store.search([1.0, 0.0], tenant_id="alpha")) == 1
    assert len(worker.fulltext_store.search("evidence", tenant_id="alpha")) == 1
    assert [r.id for r in graph.list_relations()] == ["shared"]
    assert graph.list_relations()[0].sources == [{"doc_id": "other"}]
    with pytest.raises(KeyError):
        lifecycle.delete(did, tenant_id="beta")
    assert worker.doc_store.get(did, tenant_id="alpha") is not None
    lifecycle.delete(did, tenant_id="alpha")
    assert worker.doc_store.get(did, tenant_id="alpha") is None
    assert worker.vector_store.search([1.0, 0.0], tenant_id="alpha") == []
    assert worker.fulltext_store.search("evidence", tenant_id="alpha") == []


def test_flaky_batch_retries_and_unauthorized_does_not(tmp_path):
    calls = []

    def embed(texts):
        calls.append(texts)
        if len(calls) <= 2:
            raise httpx.ReadTimeout("retry")
        return [[1.0, 0.0] for _ in texts]

    lifecycle, worker, tasks, _ = setup_pipeline(tmp_path, embed=embed)
    task = tasks.create("alpha", [upload(lifecycle)])
    worker.run_once()
    assert tasks.get(task.id).status == "done"
    assert len(calls[0]) == 2 and len(calls) == 4

    def unauthorized(texts):
        calls.append(texts)
        response = httpx.Response(401, request=httpx.Request("POST", "https://fake.test"))
        raise httpx.HTTPStatusError("secret response", request=response.request, response=response)

    worker.pipeline.embed_many = unauthorized
    task = tasks.create("alpha", [upload(lifecycle, "changed")])
    count = len(calls)
    worker.run_once()
    assert len(calls) == count + 1
    assert tasks.get(task.id).status == "failed"
    assert "401" in tasks.get(task.id).message


def test_fulltext_partial_reconcile_and_duplicate(tmp_path):
    class FlakyFulltext(BM25FulltextStore):
        fail = True

        def index(self, chunks):
            if self.fail:
                raise RuntimeError("offline failure")
            return super().index(chunks)

    ft = FlakyFulltext()
    lifecycle, worker, tasks, _ = setup_pipeline(tmp_path, fulltext=ft)
    first = upload(lifecycle)
    task = tasks.create("alpha", [first])
    worker.run_once()
    partial = tasks.get(task.id)
    assert partial.status == "partial" and partial.counts["fulltext_indexed"] == 0
    assert upload(lifecycle)["status"] == "duplicate"
    assert upload(lifecycle, tenant="beta")["status"] != "duplicate"
    ft.fail = False
    worker.reconcile(tenant_id="alpha")
    assert tasks.get(task.id).status == "done"


def test_restart_skips_finished_legacy_documents(tmp_path):
    lifecycle, worker, _, _ = setup_pipeline(tmp_path)
    upload(lifecycle)
    worker.task_store = None
    assert len(worker.run_once()) == 1
    restarted = IngestWorker(
        doc_store=FileDocStore(tmp_path),
        vector_store=worker.vector_store,
        fulltext_store=worker.fulltext_store,
        embed_many=lambda _: pytest.fail("finished doc reprocessed"),
    )
    assert restarted.run_once() == []


def test_incomplete_batch_resumes_without_reembedding_finished_chunks(tmp_path):
    calls = []

    def embed(texts):
        calls.append(texts)
        if len(calls) == 2:
            raise ValueError("permanent malformed batch")
        return [[1.0, 0.0] for _ in texts]

    lifecycle, worker, tasks, _ = setup_pipeline(tmp_path, embed=embed)
    task = tasks.create("alpha", [upload(lifecycle)])
    worker.run_once()
    assert tasks.get(task.id).status == "partial"
    assert tasks.get(task.id).counts["embedded"] == 2
    worker.doc_store = FileDocStore(tmp_path)
    worker.pipeline.docs = worker.doc_store
    worker.reconcile(tenant_id="alpha")
    assert tasks.get(task.id).status == "done"
    assert len(calls) == 3 and calls[2] == calls[1]


def test_reindex_preserves_old_vectors_on_failure_and_changes_only_target_tenant(tmp_path):
    lifecycle, worker, tasks, _ = setup_pipeline(tmp_path)
    first = upload(lifecycle)
    other = upload(lifecycle, tenant="beta")
    tasks.create("alpha", [first])
    tasks.create("beta", [other])
    worker.run_once()
    old = worker.vector_store.search([1.0, 0.0], tenant_id="alpha")
    worker.config.embedding_model = "new-model"
    worker.pipeline.embed_many = lambda _: (_ for _ in ()).throw(ValueError("bad model"))
    assert worker.reindex(tenant_id="alpha")[0].error
    assert worker.vector_store.search([1.0, 0.0], tenant_id="alpha") == old
    worker.pipeline.embed_many = lambda texts: [[0.0, 1.0, 0.0] for _ in texts]
    result = worker.reindex(tenant_id="alpha")[0]
    assert not result.error
    doc = worker.doc_store.get(first["doc_id"], tenant_id="alpha")
    assert all(c.embedding_model == "new-model" for c in doc.chunks)
    assert all(c.fulltext_indexed for c in doc.chunks)
    other_doc = worker.doc_store.get(other["doc_id"], tenant_id="beta")
    assert all(c.embedding_model == "custom" for c in other_doc.chunks)


def test_interrupted_replacement_and_delete_are_recoverable(tmp_path):
    lifecycle, worker, _, _ = setup_pipeline(tmp_path)
    first = upload(lifecycle)
    worker.process_doc(first["doc_id"], tenant_id="alpha")
    original = lifecycle.fulltext.delete_by_doc
    lifecycle.fulltext.delete_by_doc = lambda *_: (_ for _ in ()).throw(OSError("disk"))
    with pytest.raises(OSError):
        upload(lifecycle, "new text")
    pending = worker.doc_store.get(first["doc_id"], tenant_id="alpha")
    assert pending.metadata["lifecycle"] == "replacing"
    lifecycle.fulltext.delete_by_doc = original
    worker.reconcile(tenant_id="alpha")
    doc = worker.doc_store.get(first["doc_id"], tenant_id="alpha")
    assert doc.content == "new text" and doc.version == 2
    lifecycle.fulltext.delete_by_doc = lambda *_: (_ for _ in ()).throw(OSError("disk"))
    with pytest.raises(OSError):
        lifecycle.delete(first["doc_id"], tenant_id="alpha")
    lifecycle.fulltext.delete_by_doc = original
    worker.reconcile(tenant_id="alpha")
    assert worker.doc_store.get(first["doc_id"], tenant_id="alpha") is None


def test_migration_is_idempotent_and_preserves_text(tmp_path):
    import json

    (tmp_path / "old.json").write_text(
        json.dumps(
            {"doc_id": "old.txt", "title": "old", "content": "exact\ncontent", "tenant_id": "alpha"}
        )
    )
    lifecycle, worker, _, _ = setup_pipeline(tmp_path)
    worker.process_doc("old.txt", tenant_id="alpha")
    row = lifecycle.migrate(tenant_id="alpha")[0]
    uuid.UUID(row["doc_id"])
    assert row["old_id"] == "old.txt"
    assert lifecycle.docs.get("old.txt", tenant_id="alpha") is None
    doc = FileDocStore(tmp_path).get(row["doc_id"], tenant_id="alpha")
    assert doc.content == "exact\ncontent" and doc.metadata["legacy_doc_id"] == "old.txt"
    assert lifecycle.migrate(tenant_id="alpha")[0] == row
    assert worker.vector_store.search([1.0, 0.0], tenant_id="alpha") == []
    worker.reconcile(tenant_id="alpha")
    assert worker.vector_store.search([1.0, 0.0], tenant_id="alpha")


def test_list_and_unscoped_get_only_decode_the_selected_document(tmp_path, monkeypatch):
    import agentic_graphrag.stores.doc_store as module

    store = FileDocStore(tmp_path)
    for i in range(25):
        store.save(DocumentRecord(str(i), "title", "body", tenant_id="alpha"))
    calls = []
    decode = module.decode_document
    monkeypatch.setattr(
        module, "decode_document", lambda payload: (calls.append(payload), decode(payload))[1]
    )
    assert len(store.list_ids()) == 25 and not calls
    assert store.get("3").doc_id == "3" and len(calls) == 1


def test_retry_policy_429_5xx_and_401():
    from agentic_graphrag.knowledge.embedding_batch import retryable

    for status in (429, 500, 503, 401, 400):
        response = httpx.Response(status, request=httpx.Request("POST", "https://fake.test"))
        error = httpx.HTTPStatusError("failure", request=response.request, response=response)
        assert retryable(error) == (status in {429, 500, 503})


def test_actual_graph_provenance_survives_separate_ingests(tmp_path):
    from agentic_graphrag.core.ports.stores import ChunkRecord
    from agentic_graphrag.knowledge.graph_ingest import GraphIngestDeps, GraphIngestor

    lifecycle, _, _, graph = setup_pipeline(tmp_path)
    ingestor = GraphIngestor(GraphIngestDeps(graph=graph))
    for doc_id in ("one", "two"):
        ingestor.ingest_chunks(
            [ChunkRecord(doc_id + ":0", doc_id, "Kenji Watanabe founded SiliconForge in 2009.", 0)],
            tenant_id="alpha",
        )
    graph.delete_by_doc("one", "alpha")
    assert len(graph.list_relations()) == 1
    assert graph.list_relations()[0].sources[0]["doc_id"] == "two"
    graph.delete_by_doc("two", "alpha")
    assert not graph.list_relations()


def test_task_claims_are_shared_and_unauthorized_reconcile_attempts_once(tmp_path):
    lifecycle, worker, tasks, _ = setup_pipeline(tmp_path)
    row = upload(lifecycle, "small")
    task = tasks.create("alpha", [row])
    other = IngestTaskStore(tmp_path / "tasks.jsonl")
    assert other.pending()[0].id == task.id
    tasks.transition(task.id, IngestStatus.EXTRACTING, owner="first")
    with pytest.raises(ValueError):
        other.transition(task.id, IngestStatus.EXTRACTING, owner="second")
    tasks.transition(task.id, IngestStatus.FAILED, owner="first")
    calls = []

    def unauthorized(texts):
        calls.append(texts)
        response = httpx.Response(401, request=httpx.Request("POST", "https://fake.test"))
        raise httpx.HTTPStatusError("failure", request=response.request, response=response)

    worker.pipeline.embed_many = unauthorized
    worker.reconcile(tenant_id="alpha")
    assert len(calls) == 1
    assert other.get(task.id).status == "failed"


def test_reusing_document_record_resets_old_chunk_state(tmp_path):
    lifecycle, worker, _, _ = setup_pipeline(tmp_path)
    row = upload(lifecycle)
    worker.process_doc(row["doc_id"], tenant_id="alpha")
    doc = worker.doc_store.get(row["doc_id"], tenant_id="alpha")
    doc.content = "short replacement"
    lifecycle.accept(doc)
    worker.process_doc(row["doc_id"], tenant_id="alpha")
    updated = worker.doc_store.get(row["doc_id"], tenant_id="alpha")
    assert len(updated.chunks) == 1 and updated.chunks[0].text == "short replacement"


def test_stale_task_requeue_checks_latest_persisted_heartbeat(tmp_path, monkeypatch):
    import agentic_graphrag.knowledge.ingest_tasks as module

    monkeypatch.setattr(module.time, "time", lambda: 100)
    first = IngestTaskStore(tmp_path / "tasks.jsonl")
    task = first.create("alpha", [{"doc_id": "doc"}])
    first.transition(task.id, IngestStatus.EXTRACTING, owner="active")
    other = IngestTaskStore(tmp_path / "tasks.jsonl")
    monkeypatch.setattr(module.time, "time", lambda: 200)
    first.touch(task.id, owner="active")
    assert other.requeue_stale(stale_seconds=20) == []
    monkeypatch.setattr(module.time, "time", lambda: 300)
    assert other.requeue_stale(stale_seconds=20) == [task.id]
    assert first.get(task.id).status == "queued"
