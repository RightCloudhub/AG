"""Checkpoint isolation and migration of pre-normalization document databases."""

import json
import sqlite3
from dataclasses import asdict

import pytest

from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord
from agentic_graphrag.stores.doc_store import FileDocStore, InMemoryDocStore


def document(tenant="alpha", version=1):
    return DocumentRecord(
        "doc",
        "title",
        "evidence",
        tenant_id=tenant,
        version=version,
        chunks=[ChunkRecord("doc:0", "doc", "evidence", 0, tenant_id=tenant)],
    )


@pytest.mark.parametrize("memory", [False, True])
def test_chunk_checkpoint_is_scoped_and_rejects_obsolete_versions(tmp_path, memory):
    store = InMemoryDocStore() if memory else FileDocStore(tmp_path)
    doc, other = document(), document("beta")
    store.save(doc)
    store.save(other)
    doc.chunks[0].embedded = True
    doc.chunks[0].embedding = [1.0]
    doc.content = "must not be saved by a checkpoint"
    store.save_chunks(doc, doc.chunks)
    assert store.get("doc", tenant_id="alpha").content == "evidence"
    assert store.get("doc", tenant_id="alpha").chunks[0].embedding == [1.0]
    assert not store.get("doc", tenant_id="beta").chunks[0].embedded
    doc.chunks[0].embedding = [0.0, 1.0]
    with pytest.raises(ValueError):
        store.save_chunks(doc, [doc.chunks[0], ChunkRecord("missing", "doc", "text", 1)])
    assert store.get("doc", tenant_id="alpha").chunks[0].embedding == [1.0]
    store.save(document(version=2))
    with pytest.raises(ValueError):
        store.save_chunks(doc, doc.chunks)
    assert not store.get("doc", tenant_id="alpha").chunks[0].embedded
    store.delete("doc", tenant_id="alpha")
    with pytest.raises(ValueError):
        store.save_chunks(doc, doc.chunks)
    assert store.get("doc", tenant_id="alpha") is None
    assert store.get("doc", tenant_id="beta") == other


def test_existing_sqlite_payload_is_migrated_without_losing_progress(tmp_path):
    doc = document()
    doc.chunks[0].embedded = True
    doc.chunks[0].embedding = [1.0, 0.0]
    with sqlite3.connect(tmp_path / "documents.sqlite3") as db:
        db.execute(
            "CREATE TABLE documents (tenant TEXT, id TEXT, source TEXT, hash TEXT, "
            "payload TEXT, PRIMARY KEY(tenant,id))"
        )
        db.execute(
            "INSERT INTO documents VALUES (?,?,?,?,?)",
            ("alpha", "doc", "", "", json.dumps(asdict(doc))),
        )
    store = FileDocStore(tmp_path)
    assert store.get("doc", tenant_id="alpha") == doc
    assert FileDocStore(tmp_path).get("doc", tenant_id="alpha") == doc
    with sqlite3.connect(store.path) as db:
        original_payload = db.execute("SELECT payload FROM documents").fetchone()[0]
    assert json.loads(original_payload)["chunks"] == []
    doc.chunks[0].fulltext_indexed = True
    store.save_chunks(doc, doc.chunks)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT payload FROM documents").fetchone()[0] == original_payload
    assert FileDocStore(tmp_path).get("doc", tenant_id="alpha").chunks[0].fulltext_indexed
