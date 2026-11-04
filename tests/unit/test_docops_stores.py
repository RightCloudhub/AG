"""Doc store schema v2, command transactions, leases and visibility (K-01/K-04/C-04)."""

import json
import sqlite3
import time
from dataclasses import replace

import pytest

from agentic_graphrag.core.evidence import Candidate, Citation
from agentic_graphrag.core.ports.document_ops import (
    ChunkPatch,
    Command,
    CommandScope,
    DocOperation,
    DocumentQuery,
    IdempotencyConflict,
    RevisionConflict,
    TargetNotFound,
    payload_hash,
)
from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord
from agentic_graphrag.core.ports.visibility import AllVisible, filter_visible_candidates
from agentic_graphrag.stores.chunk_visibility import chunk_visibility_for
from agentic_graphrag.stores.doc_schema import SCHEMA_VERSION
from agentic_graphrag.stores.doc_store import FileDocStore, InMemoryDocStore


def make_doc(doc_id="d1", tenant="alpha", n=3, version=1, title="Guide"):
    chunks = [
        ChunkRecord(
            f"{doc_id}:{i}",
            doc_id,
            f"text {i}",
            i,
            metadata={"title": title, "heading_path": ["H"], "document_version": version},
            tenant_id=tenant,
        )
        for i in range(n)
    ]
    return DocumentRecord(
        doc_id,
        title,
        "body",
        tenant_id=tenant,
        source_uri=f"upload://{title}",
        version=version,
        updated_at=f"2026-01-0{n}T00:00:00",
        chunks=chunks,
        metadata={"chunks_initialized": True},
    )


def command(tenant="alpha", key="k1", payload=None, target="d1:0"):
    payload = payload or {"enabled": False, "expected_revision": 1}
    op = DocOperation("op-" + key, tenant, "d1", "chunk_patch", "task-" + key, {"chunk_id": target})
    scope = CommandScope(tenant, "chunk_patch", f"d1/{target}", key)
    return Command(scope, payload_hash(payload), op)


def respond(chunk, op):
    return {"revision": chunk.revision, "enabled": chunk.enabled, "task_id": op.task_id}


def patch(**kwargs):
    base = {"tenant_id": "alpha", "doc_id": "d1", "chunk_id": "d1:0", "expected_revision": 1}
    return ChunkPatch(**{**base, **kwargs})


def test_legacy_store_migrates_to_v2_with_enabled_revision_one(tmp_path):
    db = sqlite3.connect(tmp_path / "documents.sqlite3")
    db.execute(
        "CREATE TABLE documents (tenant TEXT, id TEXT, source TEXT, hash TEXT, payload TEXT, "
        "PRIMARY KEY(tenant,id))"
    )
    db.execute(
        "CREATE TABLE chunks (tenant TEXT, doc_id TEXT, id TEXT, version INTEGER, "
        "position INTEGER, payload TEXT, PRIMARY KEY(tenant,doc_id,id))"
    )
    doc = {"doc_id": "old", "title": "Legacy", "content": "x", "tenant_id": "alpha", "version": 2}
    db.execute("INSERT INTO documents VALUES ('alpha','old','s','h',?)", (json.dumps(doc),))
    chunk = {"chunk_id": "old:0", "doc_id": "old", "text": "legacy text", "index": 0}
    db.execute("INSERT INTO chunks VALUES ('alpha','old','old:0',2,0,?)", (json.dumps(chunk),))
    db.execute("PRAGMA user_version=1")
    db.commit()
    db.close()
    store = FileDocStore(tmp_path)
    loaded = store.get("old", tenant_id="alpha")
    assert loaded.chunks[0].enabled is True and loaded.chunks[0].revision == 1
    rows, total = store.list_chunk_rows(("alpha", "old"), (10, 0))
    assert total == 1 and rows[0]["enabled"] and rows[0]["revision"] == 1
    assert store.document_row("alpha", "old")["title"] == "Legacy"
    with sqlite3.connect(tmp_path / "documents.sqlite3") as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        # A pre-v2 writer's positional insert cannot write the widened table.
        with pytest.raises(sqlite3.OperationalError):
            raw.execute("INSERT INTO documents VALUES ('a','b','c','d','e')")
        raw.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="newer"):
        FileDocStore(tmp_path)


def test_checkpoints_and_saves_never_overwrite_a_newer_revision(tmp_path):
    store = FileDocStore(tmp_path)
    doc = make_doc()
    store.save(doc)
    store.apply_chunk_patch(patch(text="edited"), command(payload={"t": 1}), respond)
    doc.chunks[0].embedded = doc.chunks[1].embedded = True
    store.save_chunks(doc, doc.chunks[:2])
    store.save(doc)  # a worker's final save from its old snapshot
    current = store.get("d1", tenant_id="alpha")
    assert current.chunks[0].text == "edited" and current.chunks[0].revision == 2
    assert current.chunks[1].embedded is True
    with pytest.raises(ValueError, match="obsolete"):
        store.save_chunks(replace(doc, version=9), [doc.chunks[1]])
    memory = InMemoryDocStore()
    memory.save(doc)
    newer = replace(doc.chunks[0], revision=3, text="new")
    memory.save_chunks(doc, [newer])
    memory.save_chunks(doc, [replace(doc.chunks[0], text="old")])
    assert memory.get("d1", tenant_id="alpha").chunks[0].text == "new"


def test_patch_commits_intent_generation_op_and_idempotency_atomically(tmp_path):
    store = FileDocStore(tmp_path)
    store.save(make_doc())
    before = store.tenant_generation("alpha")
    outcome = store.apply_chunk_patch(patch(enabled=False), command(), respond)
    assert outcome.response == {"revision": 2, "enabled": False, "task_id": "task-k1"}
    assert store.tenant_generation("alpha") == before + 1
    assert [op.op_id for op in store.undispatched_ops(10)] == ["op-k1"]
    assert store.mutation_seq("alpha", "d1") == 1
    restarted = FileDocStore(tmp_path)
    replayed = restarted.apply_chunk_patch(patch(enabled=False), command(), respond)
    assert replayed.replayed and replayed.response == outcome.response
    with pytest.raises(IdempotencyConflict):
        restarted.apply_chunk_patch(patch(), command(payload={"other": 1}), respond)
    with pytest.raises(RevisionConflict) as conflict:
        restarted.apply_chunk_patch(patch(), command(key="k2"), respond)
    assert conflict.value.details == {"current_revision": 2}
    with pytest.raises(TargetNotFound):
        restarted.apply_chunk_patch(patch(tenant_id="beta"), command("beta", "k3"), respond)
    with pytest.raises(TargetNotFound):
        restarted.apply_chunk_patch(patch(chunk_id="nope"), command(key="k4"), respond)
    assert len(restarted.undispatched_ops(10)) == 1  # failed commands left nothing behind


def test_lease_fencing_tokens_and_fenced_stage_commits(tmp_path, monkeypatch):
    import agentic_graphrag.stores.doc_ops_sql as sql

    store = FileDocStore(tmp_path)
    doc = make_doc()
    store.save(doc)
    clock = [1000.0]
    monkeypatch.setattr(sql.time, "time", lambda: clock[0])
    first = store.acquire_lease(("alpha", "d1"), owner="a", ttl=10)
    assert store.acquire_lease(("alpha", "d1"), owner="b", ttl=10) is None
    assert store.acquire_lease(("alpha", "d1"), owner="a", ttl=10) == first
    clock[0] += 11
    assert not store.fence_valid(first) and not store.renew_lease(first, ttl=10)
    second = store.acquire_lease(("alpha", "d1"), owner="b", ttl=10)
    assert second.token == first.token + 1
    ready = replace(doc.chunks[0], embedded=True)
    assert not store.commit_chunk_stage(ready, fence=first, doc=doc)
    assert store.commit_chunk_stage(ready, fence=second, doc=doc)
    stale_revision = replace(ready, revision=7)
    assert not store.commit_chunk_stage(stale_revision, fence=second, doc=doc)
    store.release_lease(second)
    assert store.acquire_lease(("alpha", "d1"), owner="c", ttl=10).token == second.token + 1


def test_visibility_is_authoritative_for_managed_documents_only(tmp_path):
    store = FileDocStore(tmp_path)
    store.save(make_doc())
    store.save(make_doc("gone", n=1))
    busy = make_doc("busy", n=1)
    busy.metadata["lifecycle"] = "deleting"
    store.save(busy)
    store.delete("gone", tenant_id="alpha")
    store.apply_chunk_patch(patch(enabled=False), command(), respond)
    vis = chunk_visibility_for(store)
    refs = [("d1", "d1:0"), ("d1", "d1:1"), ("d1", "d1:9"), ("gone", "gone:0")]
    refs += [("busy", "busy:0"), ("seed", "seed:0"), ("", "orphan")]
    assert vis.visible("alpha", refs) == {("d1", "d1:1"), ("seed", "seed:0"), ("", "orphan")}
    assert vis.visible("beta", [("d1", "d1:0")]) == {("d1", "d1:0")}  # unmanaged there
    assert vis.current_revisions("alpha", [("d1", "d1:1")]) == {("d1", "d1:1"): (1, 1)}
    assert vis.generation("alpha") >= 2 and vis.generation("beta") == 0
    assert isinstance(chunk_visibility_for(InMemoryDocStore()), AllVisible)


def chunk_candidate(cid, doc="d1", **meta):
    return Candidate(
        id=cid,
        source="vector_chunk",
        content="x",
        citations=[Citation(doc_id=doc, chunk_id=cid)],
        metadata=meta,
    )


def test_filter_visible_candidates_drops_hidden_and_stale_evidence(tmp_path):
    store = FileDocStore(tmp_path)
    store.save(make_doc())
    store.apply_chunk_patch(patch(enabled=False), command(), respond)
    graph = Candidate(
        id="g",
        source="graph_neighbor",
        content="fact",
        citations=[
            Citation(entity_id="e"),
            Citation(doc_id="d1", chunk_id="d1:0"),
            Citation(doc_id="d1", chunk_id="d1:1"),
        ],
    )
    only_hidden = graph.model_copy(update={"id": "h", "citations": graph.citations[:2]})
    seed = Candidate(
        id="s", source="graph_path", content="seed", citations=[Citation(entity_id="e")]
    )
    pool = [
        chunk_candidate("d1:0"),
        chunk_candidate("d1:1", doc_version=1, chunk_revision=1),
        chunk_candidate("d1:2", chunk_revision=0),
        graph,
        only_hidden,
        seed,
    ]
    kept = filter_visible_candidates(pool, chunk_visibility_for(store), "alpha")
    assert [c.id for c in kept] == ["d1:1", "g", "s"]
    assert [c.chunk_id for c in kept[1].citations if c.chunk_id] == ["d1:1"]
    assert len(graph.citations) == 3  # inputs are not mutated
    assert filter_visible_candidates(pool, AllVisible(), "alpha") == pool
    assert filter_visible_candidates([seed], chunk_visibility_for(store), "alpha") == [seed]


def test_document_projection_pages_filters_and_isolates_tenants(tmp_path):
    store = FileDocStore(tmp_path)
    for i in range(1, 6):
        doc = make_doc(f"d{i}", n=i, title=f"Doc {i}")
        doc.metadata["index_complete"] = i % 2 == 0
        doc.metadata["index_error"] = "embedding_failed: 503" if i == 5 else ""
        store.save(doc)
    store.save(make_doc("other", tenant="beta", title="Doc beta"))
    rows, total = store.list_documents(DocumentQuery("alpha", limit=2, offset=1))
    assert total == 5 and [r["doc_id"] for r in rows] == ["d4", "d3"]
    ready, n_ready = store.list_documents(DocumentQuery("alpha", status="ready"))
    assert n_ready == 2 and {r["doc_id"] for r in ready} == {"d2", "d4"}
    failed, _ = store.list_documents(DocumentQuery("alpha", status="failed"))
    assert [r["doc_id"] for r in failed] == ["d5"]
    found, n_found = store.list_documents(DocumentQuery("alpha", query="doc 3"))
    assert n_found == 1 and found[0]["total"] == 3 and found[0]["enabled"] == 3
    assert store.list_documents(DocumentQuery("alpha", query="%"))[1] == 0
    assert store.document_row("beta", "d1") is None
    assert store.document_row("alpha", "d3")["stages"]["vector"]["indexed"] == 0
    chunks, n_chunks = store.list_chunk_rows(("alpha", "d3"), (2, 1))
    assert n_chunks == 3 and [c["ordinal"] for c in chunks] == [1, 2]
    assert chunks[0]["breadcrumb"] == ["Doc 3", "H"] and chunks[0]["text_preview"] == "text 1"
    assert "embedding" not in json.dumps(chunks)
    assert store.chunk_detail_row(("beta", "d3", "d3:0")) is None


def test_async_parse_reservations_are_listed_until_published(tmp_path):
    store = FileDocStore(tmp_path)
    op = DocOperation("op", "alpha", "new-id", "parse", "task", {"files": [{"doc_id": "new-id"}]})
    reservation = {"doc_id": "new-id", "title": "a.txt", "source": "upload://a.txt"}
    store.stage_parse(op, [reservation])
    store.stage_parse(op, [reservation])  # duplicate completion: one operation
    assert len(store.undispatched_ops(10)) == 1
    rows, total = store.list_documents(DocumentQuery("alpha"))
    assert total == 1 and rows[0]["status"] == "parsing" and rows[0]["reserved"]
    store.set_reservations("op", state="failed", error="bad bytes")
    assert store.document_row("alpha", "new-id")["status"] == "parse_failed"
    store.set_reservations("op", state="published")
    assert store.document_row("alpha", "new-id") is None
    assert time.time() > 0
