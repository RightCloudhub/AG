"""Chunk edit/disable/re-enable through the durable command + worker path (K-04/K-05)."""

import pytest
from docops_support import make_env

from agentic_graphrag.core.enums import IngestStatus
from agentic_graphrag.core.ports.document_ops import IdempotencyConflict, RevisionConflict
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.stores.chunk_visibility import chunk_visibility_for


def test_edit_reindexes_every_channel_and_keeps_facts_supported_elsewhere(tmp_path):
    env = make_env(tmp_path)
    did = env.upload()
    first, second = env.docs.get(did, tenant_id="alpha").chunks
    assert env.relations()[("Beta", "Gadget")] == sorted([first.chunk_id, second.chunk_id])
    outcome = env.patch(did, 0, key="edit", text="Acme PRODUCES Gizmo.")
    assert outcome.response["revision"] == 2
    assert env.search("Widget")  # saved, not yet index-ready: old revision still indexed
    results = env.worker.run_once()
    assert [r.error for r in results] == [None]
    assert env.search("Widget") == [] and env.search("Gizmo") == ["Acme PRODUCES Gizmo."]
    hits = {c.chunk_id: c for c, _ in env.vector.search([1.0] * 8, tenant_id="alpha")}
    assert (
        hits[first.chunk_id].text == "Acme PRODUCES Gizmo." and hits[first.chunk_id].revision == 2
    )
    relations = env.relations()
    assert ("Acme", "Widget") not in relations
    assert relations[("Acme", "Gizmo")] == [first.chunk_id]
    assert relations[("Beta", "Gadget")] == [second.chunk_id]  # shared fact survives
    chunk = env.docs.get(did, tenant_id="alpha").chunks[0]
    assert {s: chunk.stages[s]["state"] for s in chunk.stages} == {
        "vector": "ready",
        "fulltext": "ready",
        "graph": "ready",
    }
    assert all(chunk.stages[s]["revision"] == 2 for s in chunk.stages)
    task = env.tasks.get(outcome.op.task_id, tenant_id="alpha")
    assert task.status == IngestStatus.DONE
    assert env.docs.get_op(outcome.op.op_id).state == "done"


def test_disable_hides_immediately_and_reenable_rebuilds_without_reembedding(tmp_path):
    env = make_env(tmp_path)
    did = env.upload()
    first, second = env.docs.get(did, tenant_id="alpha").chunks
    vis = chunk_visibility_for(env.docs)
    generation = vis.generation("alpha")
    env.patch(did, 1, key="off", enabled=False)
    assert vis.visible("alpha", [(did, second.chunk_id)]) == set()
    assert vis.generation("alpha") > generation
    env.worker.run_once()
    assert env.search("Gadget") == [first.text]
    assert second.chunk_id not in {c.chunk_id for c, _ in env.vector.search([1.0] * 8)}
    assert env.relations()[("Beta", "Gadget")] == [first.chunk_id]  # still supported by chunk 0
    calls = len(env.embedder.calls)
    env.patch(did, 1, key="on", enabled=True)
    env.worker.run_once()
    assert len(env.embedder.calls) == calls  # unchanged text reuses its embedding
    assert sorted(env.search("Gadget")) == sorted([first.text, second.text])
    assert env.relations()[("Beta", "Gadget")] == sorted([first.chunk_id, second.chunk_id])
    assert vis.visible("alpha", [(did, second.chunk_id)]) == {(did, second.chunk_id)}
    stored = env.docs.get(did, tenant_id="alpha").chunks[1]
    assert stored.enabled and stored.revision == 3 and stored.fulltext_indexed


def test_revision_conflicts_and_idempotency_keys(tmp_path):
    env = make_env(tmp_path)
    did = env.upload()
    first = env.patch(did, 0, key="k", enabled=False)
    again = env.patch(did, 0, key="k", enabled=False, expected_revision=1)
    assert again.replayed and again.response == first.response
    with pytest.raises(IdempotencyConflict):
        env.patch(did, 0, key="k", enabled=True, expected_revision=1)
    with pytest.raises(RevisionConflict):
        env.patch(did, 0, key="other", enabled=True, expected_revision=1)
    _, total = env.tasks.list(tenant_id="alpha", limit=100)
    assert total == 2  # the upload and exactly one command task


def test_crash_between_intent_and_dispatch_recovers_exactly_one_task(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    did = env.upload()

    def crash(*args, **kwargs):
        raise OSError("journal unavailable")

    monkeypatch.setattr(env.tasks, "ensure", crash)
    outcome = env.patch(did, 0, key="crash", text="Acme PRODUCES Gizmo.")
    assert env.tasks.get(outcome.op.task_id) is None  # durable intent, no task yet
    monkeypatch.undo()
    marked = []
    update = env.docs.update_op

    def lost_ack(op_id, **fields):
        if fields.get("dispatched") and not marked:
            marked.append(op_id)
            raise OSError("crash after the task was written")
        return update(op_id, **fields)

    monkeypatch.setattr(env.docs, "update_op", lost_ack)
    assert env.commands.dispatch_pending() == 0  # task written, acknowledgement lost
    monkeypatch.undo()
    env.worker.run_once()
    tasks, total = env.tasks.list(tenant_id="alpha", limit=100)
    assert total == 2 and outcome.op.task_id in {t.id for t in tasks}
    assert env.tasks.get(outcome.op.task_id).status == IngestStatus.DONE
    assert env.search("Gizmo") and env.docs.undispatched_ops(10) == []


def test_document_lease_serializes_and_fences_a_blocked_worker(tmp_path, monkeypatch):
    import agentic_graphrag.knowledge.ingest_tasks as task_module
    import agentic_graphrag.stores.doc_ops_sql as lease_module

    env = make_env(tmp_path, stale_task_seconds=10)
    did = env.upload()
    clock = [10_000.0]
    monkeypatch.setattr(lease_module.time, "time", lambda: clock[0])
    monkeypatch.setattr(task_module.time, "time", lambda: clock[0])
    outcome = env.patch(did, 0, key="edit", text="Acme PRODUCES Gizmo.")
    rescuer = env.new_worker("worker-rescue", stale_task_seconds=10)

    def stall():
        # Worker A is blocked in the provider call past both lease TTLs; B takes over.
        env.embedder.hook = None
        clock[0] += 60
        assert [r.error for r in rescuer.run_once()] == [None]

    env.embedder.hook = stall
    assert env.worker.run_once() == []  # A lost its lease: it publishes nothing
    op = env.docs.get_op(outcome.op.op_id)
    assert op.state == "done" and op.error == ""
    assert env.search("Gizmo") == ["Acme PRODUCES Gizmo."]
    chunk = env.docs.get(did, tenant_id="alpha").chunks[0]
    assert chunk.stages["vector"]["generation"] > 1 and chunk.embedded
    assert env.tasks.get(outcome.op.task_id).status == IngestStatus.DONE


def test_disable_is_prompt_while_indexing_is_blocked(tmp_path):
    import threading
    import time

    env = make_env(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        release.wait(timeout=5)

    env.embedder.hook = block
    row = env.worker.pipeline.lifecycle.accept(
        DocumentRecord("", "guide.md", "# Alpha\n\nAcme PRODUCES Widget.\n", tenant_id="alpha")
    )
    env.tasks.create("alpha", [row])
    worker = threading.Thread(target=env.worker.run_once)
    worker.start()
    try:
        assert entered.wait(timeout=5)
        started = time.monotonic()
        chunk = env.docs.get(row["doc_id"], tenant_id="alpha").chunks[0]
        env.patch(row["doc_id"], 0, key="off", enabled=False)
        assert time.monotonic() - started < 2.0  # no wait on the indexing document lock
        vis = chunk_visibility_for(env.docs)
        assert vis.visible("alpha", [(row["doc_id"], chunk.chunk_id)]) == set()
    finally:
        env.embedder.hook = None
        release.set()
        worker.join(timeout=5)
    env.worker.run_once()
    assert env.vector.search([1.0] * 8, tenant_id="alpha") == []
    assert env.search("Widget") == []
    stored = env.docs.get(row["doc_id"], tenant_id="alpha").chunks[0]
    assert not stored.enabled and stored.stages["vector"]["state"] == "disabled"


def test_stage_failure_is_recorded_and_retried_by_reconcile(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    did = env.upload()
    outcome = env.patch(did, 0, key="edit", text="Acme PRODUCES Gizmo.")

    def down(chunks):
        raise OSError("bm25 snapshot unavailable")

    monkeypatch.setattr(env.fulltext, "index", down)
    results = env.worker.run_once()
    assert results[0].error == "fulltext_failed: OSError"
    chunk = env.docs.get(did, tenant_id="alpha").chunks[0]
    assert chunk.stages["fulltext"]["state"] == "failed" and chunk.embedded
    assert env.tasks.get(outcome.op.task_id).status == IngestStatus.PARTIAL
    monkeypatch.undo()
    env.worker.reconcile(tenant_id="alpha")
    assert env.search("Gizmo") and env.docs.get_op(outcome.op.op_id).state == "done"


def test_operations_on_deleted_documents_end_in_conflict(tmp_path):
    env = make_env(tmp_path)
    did = env.upload()
    outcome = env.patch(did, 0, key="edit", text="Acme PRODUCES Gizmo.")
    env.worker.pipeline.lifecycle.delete(did, tenant_id="alpha")
    env.worker.run_once()
    assert env.docs.get_op(outcome.op.op_id).state == "conflict"
    task = env.tasks.get(outcome.op.task_id)
    assert task.status == IngestStatus.FAILED and task.message == "document_changed"
    assert env.search("Gizmo") == []
