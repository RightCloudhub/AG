"""ID-based review merges preserve source-backed history and support safe undo."""

from dataclasses import replace

import pytest
from test_open_graph import chunk, setup_open

from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.knowledge.review.executor import ReviewExecutor, ReviewExecutorDeps
from agentic_graphrag.knowledge.review.open_resolution import OpenMergeDeps, undo_open_merge
from agentic_graphrag.stores.doc_store import InMemoryDocStore


def setup_review(tmp_path):
    kb, graph, index, _llm, queue, ingest = setup_open(tmp_path, judge=False)
    first, second = chunk(kb), chunk(kb, doc_id="d2")
    ingest.ingest_chunks([first], tenant_id="t")
    original = next(e for e in graph.list_entities() if e.type == "人")
    ingest.ingest_chunks([second], tenant_id="t")
    item = next(
        i
        for i in queue.list(type="resolution")
        if graph.get_entity_by_id(i.payload["drop_id"], tenant_id="t").type == "人"
    )
    item.payload["keep_id"] = original.id
    docs = InMemoryDocStore()
    for source in [first, second]:
        docs.save(
            DocumentRecord(
                doc_id=source.doc_id,
                title="Source",
                content=source.text,
                tenant_id="t",
                kb_id=kb.kb_id,
                chunks=[source],
            )
        )
    deps = OpenMergeDeps(
        graph=graph, index=index, space="provider:model:revision:dim2", queue=queue
    )
    executor = ReviewExecutor(
        ReviewExecutorDeps(
            graph=graph,
            review_queue=queue,
            docs=docs,
            entity_index=index,
            embedding_space=deps.space,
        )
    )
    return kb, graph, index, queue, item, docs, deps, executor


def test_review_merge_redirects_old_entity_and_revert_restores_both(tmp_path):
    kb, graph, index, queue, item, docs, deps, executor = setup_review(tmp_path)
    dropped, kept = item.payload["drop_id"], item.payload["keep_id"]
    queue.decide(item.id, "approve", reviewer="human", tenant_id="t")
    outcome = executor.execute(item)
    assert outcome["applied"] and outcome["merge_before"]
    assert graph.get_entity_by_id(dropped, tenant_id="t") is None
    assert index.get(dropped, scope=kb.scope, space=deps.space).entity_id == kept
    assert any(r.head_id == kept for r in graph.list_relations())
    assert undo_open_merge(item, deps, docs=docs)["effect"] == "merge_reverted"
    assert graph.get_entity_by_id(dropped, tenant_id="t") is not None
    assert index.get(dropped, scope=kb.scope, space=deps.space).entity_id == dropped
    index.close()


def test_undo_refuses_later_related_writes_or_deleted_original_sources(tmp_path):
    _kb, graph, index, queue, item, docs, deps, executor = setup_review(tmp_path)
    queue.decide(item.id, "approve", reviewer="human", tenant_id="t")
    executor.execute(item)
    relation = next(r for r in graph.list_relations() if r.head_id == item.payload["keep_id"])
    graph.upsert_relations([replace(relation, id="later-fact")])
    with pytest.raises(ValueError, match="changed after"):
        undo_open_merge(item, deps, docs=docs)
    graph.delete_relation("later-fact", tenant_id="t")
    docs.delete("d1", tenant_id="t")
    with pytest.raises(ValueError, match="source is no longer"):
        undo_open_merge(item, deps, docs=docs)
    index.close()


def test_prepared_merge_recovers_after_index_write_failure(tmp_path, monkeypatch):
    kb, graph, index, queue, item, _docs, deps, executor = setup_review(tmp_path)
    queue.decide(item.id, "approve", reviewer="human", tenant_id="t")
    original = index.redirect

    def fail(*args, **kwargs):
        raise RuntimeError("crash after graph rewiring")

    monkeypatch.setattr(index, "redirect", fail)
    with pytest.raises(RuntimeError):
        executor.execute(item)
    assert queue.get(item.id).execution["effect"] == "merge_prepared"
    monkeypatch.setattr(index, "redirect", original)
    assert executor.execute(queue.get(item.id))["applied"]
    assert (
        index.get(item.payload["drop_id"], scope=kb.scope, space=deps.space).entity_id
        == item.payload["keep_id"]
    )
    assert len([e for e in graph.list_entities() if e.type == "人"]) == 1
    index.close()


def test_ambiguous_approval_stays_pending_until_valid_target_is_selected(tmp_path):
    _kb, graph, index, queue, item, _docs, _deps, executor = setup_review(tmp_path)
    keep = item.payload["keep_id"]
    item.payload["keep_id"] = None
    for selection in (None, "not-a-candidate", item.payload["drop_id"]):
        with pytest.raises(ValueError, match="candidate"):
            queue.decide(item.id, "approve", tenant_id="t", keep_id=selection)
        assert queue.get(item.id).status == "pending"
        assert queue.get(item.id).execution == {}
    approved = queue.decide(item.id, "approve", tenant_id="t", keep_id=keep)
    assert executor.execute(approved)["applied"]
    assert graph.get_entity_by_id(item.payload["drop_id"], tenant_id="t") is None
    assert executor.execute(approved)["completed"]
    index.close()


def test_unresolved_legacy_approval_is_not_marked_completed(tmp_path):
    _kb, _graph, index, _queue, item, _docs, _deps, executor = setup_review(tmp_path)
    item.payload["keep_id"] = None
    item.status = "approved"
    with pytest.raises(ValueError, match="candidate"):
        executor.execute(item)
    assert not item.execution.get("completed")
    index.close()
