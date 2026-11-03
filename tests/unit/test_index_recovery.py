"""Regression coverage for restart recovery, bounded checkpoints and review state."""

import json
from dataclasses import asdict

import pytest

from agentic_graphrag.app.documents import lifecycle_for
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.app.review_app import ReviewApp
from agentic_graphrag.app.write_path import build_inline_worker
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph
from agentic_graphrag.knowledge.graph_ingest import GraphIngestDeps, GraphIngestor
from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore
from agentic_graphrag.knowledge.ingest_worker import IngestWorker, IngestWorkerConfig
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
from agentic_graphrag.stores.doc_store import FileDocStore
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore


def worker_at(root, *, embed=None, tasks=None):
    return IngestWorker(
        doc_store=FileDocStore(root),
        vector_store=InMemoryVectorStore(),
        fulltext_store=BM25FulltextStore(),
        graph_ingestor=GraphIngestor(GraphIngestDeps(graph=InMemoryGraphStore())),
        embed_many=embed or (lambda texts: [[1.0, 0.0] for _ in texts]),
        task_store=tasks,
        config=IngestWorkerConfig(
            chunk_size_tokens=16, chunk_overlap_tokens=0, embedding_batch_size=2
        ),
    )


def accept(worker, text):
    return worker.pipeline.lifecycle.accept(
        DocumentRecord("", "a.txt", text, tenant_id="alpha", source_uri="upload://a.txt")
    )


@pytest.mark.parametrize("interrupted", [False, True])
def test_fresh_memory_indexes_recover_persisted_stages(tmp_path, interrupted):
    calls = []

    def embed(texts):
        calls.append(texts)
        if interrupted and len(calls) == 2:
            raise ValueError("invalid batch")
        return [[1.0, 0.0] for _ in texts]

    tasks = IngestTaskStore(tmp_path / "tasks.jsonl")
    first = worker_at(tmp_path, embed=embed, tasks=tasks)
    row = accept(first, "Kenji Watanabe founded SiliconForge in 2009.\n\n" * 4)
    task = tasks.create("alpha", [row])
    first.run_once()
    assert tasks.get(task.id).status == ("partial" if interrupted else "done")
    recovered_calls = []

    def recover_embed(texts):
        recovered_calls.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    restarted = worker_at(tmp_path, embed=recover_embed, tasks=tasks)
    restarted.reconcile(tenant_id="alpha")
    doc = restarted.doc_store.get(row["doc_id"], tenant_id="alpha")
    assert len(restarted.vector_store.search([1.0, 0.0], top_k=100)) == len(doc.chunks)
    assert len(restarted.fulltext_store.search("Kenji", top_k=100)) == len(doc.chunks)
    assert restarted.graph_ingestor.graph.list_relations()
    assert len(recovered_calls) == (2 if interrupted else 0)
    assert tasks.get(task.id).status == "done"


def test_checkpoint_payload_growth_is_linear(tmp_path, monkeypatch):
    sizes = []
    for count in (8, 32):
        worker = worker_at(tmp_path / str(count))
        row = accept(worker, "word " * (12 * count))
        written = []
        save = worker.doc_store.save

        def measured(doc, written=written, save=save):
            written.append(len(json.dumps(asdict(doc))))
            save(doc)

        monkeypatch.setattr(worker.doc_store, "save", measured)
        assert not worker.process_doc(row["doc_id"], tenant_id="alpha").error
        sizes.append(sum(written))
    # Four times the text/chunks must not approach sixteen times the payload writes.
    assert sizes[1] < sizes[0] * 6


def test_failed_restoration_keeps_checkpoint_incomplete(tmp_path, monkeypatch):
    first = worker_at(tmp_path)
    row = accept(first, "evidence")
    first.run_once()
    restarted = worker_at(tmp_path, embed=lambda _: pytest.fail("cached embedding was lost"))
    original = restarted.fulltext_store.index
    monkeypatch.setattr(
        restarted.fulltext_store, "index", lambda _: (_ for _ in ()).throw(OSError("disk"))
    )
    result = restarted.reconcile(tenant_id="alpha")[0]
    assert result.error and result.counts["incomplete"] == 1
    doc = restarted.doc_store.get(row["doc_id"], tenant_id="alpha")
    assert not doc.metadata["index_complete"] and not doc.chunks[0].fulltext_indexed
    monkeypatch.setattr(restarted.fulltext_store, "index", original)
    assert not restarted.reconcile(tenant_id="alpha")[0].error
    assert restarted.fulltext_store.search("evidence", tenant_id="alpha")


@pytest.fixture
def conflict_service(tmp_path, monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_DIR", str(tmp_path))
    svc = QueryService.create_offline()
    load_triples_into_graph(
        svc.bundle.graph,
        [
            Triple(
                head=EntityMention(name="Elena Varga", type="Person"),
                relation="CEO_OF",
                tail=EntityMention(name="Apex Holdings", type="Company"),
                confidence=0.8,
                attributes={"tenant_id": "alpha"},
            )
        ],
        clear_first=False,
    )
    yield svc
    svc.close()


def queue_conflict(svc, *, fail_embedding=False):
    worker = build_inline_worker(svc)
    if fail_embedding:
        worker.pipeline.embed_many = lambda _: (_ for _ in ()).throw(ValueError("bad embedding"))
    row = lifecycle_for(svc).accept(
        DocumentRecord("", "a.txt", "The CEO of Apex Holdings is Ravi Shah.", tenant_id="alpha")
    )
    task = svc.ingest_tasks.create("alpha", [row])
    worker.run_once()
    item = svc.review_queue.list(tenant_id="alpha")[0]
    return worker, row, task, item


@pytest.mark.parametrize("action", ["delete", "replace", "pending_delete"])
def test_obsolete_review_cannot_write_or_retire_facts(conflict_service, action, monkeypatch):
    svc = conflict_service
    _, row, _, item = queue_conflict(svc)
    lifecycle = lifecycle_for(svc)
    if action == "replace":
        lifecycle.accept(DocumentRecord("", "a.txt", "replacement text", tenant_id="alpha"))
    elif action == "pending_delete":
        monkeypatch.setattr(
            lifecycle.vector, "delete_by_doc", lambda *_: (_ for _ in ()).throw(OSError("disk"))
        )
        with pytest.raises(OSError):
            lifecycle.delete(row["doc_id"], tenant_id="alpha")
    else:
        lifecycle.delete(row["doc_id"], tenant_id="alpha")
    before = svc.bundle.graph.list_relations()
    decided = ReviewApp(svc).decide(item.id, "approve", tenant_id="alpha")
    assert not decided.execution["applied"]
    assert decided.execution["effect"] == "stale_document"
    assert svc.bundle.graph.list_relations() == before


@pytest.mark.parametrize("legacy", [False, True])
def test_current_version_review_is_still_executable(conflict_service, legacy):
    svc = conflict_service
    lifecycle_for(svc).accept(DocumentRecord("", "a.txt", "old text", tenant_id="alpha"))
    _, _, task, item = queue_conflict(svc)
    assert item.payload["incoming"]["source_doc_version"] == 2
    if legacy:
        item.payload["incoming"].pop("source_doc_version")
    decided = ReviewApp(svc).decide(item.id, "approve", tenant_id="alpha")
    assert decided.execution["applied"]
    assert svc.ingest_tasks.get(task.id).status == "done"


@pytest.mark.parametrize("separate_queue", [False, True])
def test_resolved_review_does_not_strand_repaired_task(conflict_service, separate_queue):
    from agentic_graphrag.knowledge.review.queue import ReviewQueue

    svc = conflict_service
    worker, _, task, item = queue_conflict(svc, fail_embedding=True)
    assert svc.ingest_tasks.get(task.id).status == "partial"
    if separate_queue:
        svc.review_queue = ReviewQueue(svc.review_queue.path)
    ReviewApp(svc).decide(item.id, "reject", tenant_id="alpha")
    assert svc.review_queue.count(status="pending", tenant_id="alpha") == 0
    worker.pipeline.embed_many = lambda texts: [[1.0, 0.0] for _ in texts]
    worker.reconcile(tenant_id="alpha")
    assert svc.ingest_tasks.get(task.id).status == "done"


def test_review_refresh_drops_items_removed_from_the_journal(tmp_path):
    from agentic_graphrag.knowledge.review.queue import ReviewQueue

    path = tmp_path / "review.jsonl"
    queue = ReviewQueue(path)
    queue.enqueue("conflict", {"incoming": {}}, tenant_id="alpha")
    assert queue.count(status="pending", tenant_id="alpha") == 1
    path.write_text("")  # Simulate retention replacing the journal.
    assert queue.count(status="pending", tenant_id="alpha") == 0
