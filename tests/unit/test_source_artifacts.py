"""Evidence batches retain immutability and commit all records atomically."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from agentic_graphrag.app import evidence_sources
from agentic_graphrag.core.knowledge import KnowledgeScope
from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord
from agentic_graphrag.governance.source_store import SourceStore
from agentic_graphrag.stores.doc_store import InMemoryDocStore


def test_batch_uses_one_transaction_and_is_idempotent(tmp_path, monkeypatch):
    store = SourceStore(tmp_path / "sources.sqlite3")
    scope = KnowledgeScope(tenant_id="tenant", kb_id="contracts")
    records = [(f"ev-{i}", {"content": f"original {i}"}) for i in range(12)]
    connect = store._connect
    connections = []

    @contextmanager
    def counted():
        with connect() as db:
            connections.append(db)
            yield db

    monkeypatch.setattr(store, "_connect", counted)
    store.put_many(scope, records, kind="evidence")
    assert len(connections) == 1
    store.put_many(scope, records, kind="evidence")
    assert store.get(scope, "ev-11", kind="evidence") == {"content": "original 11"}
    other = KnowledgeScope(tenant_id="tenant", kb_id="other")
    assert store.get(other, "ev-11", kind="evidence") is None


def test_conflict_rolls_back_the_entire_evidence_batch(tmp_path):
    store = SourceStore(tmp_path / "sources.sqlite3")
    scope = KnowledgeScope()
    store.put(scope, {"content": "original"}, kind="evidence", key="existing")
    with pytest.raises(ValueError, match="immutable"):
        store.put_many(
            scope,
            [("new", {"content": "new"}), ("existing", {"content": "overwritten"})],
            kind="evidence",
        )
    assert store.get(scope, "new", kind="evidence") is None
    assert store.get(scope, "existing", kind="evidence") == {"content": "original"}


@pytest.mark.parametrize("available", [True, False])
def test_repeated_citations_read_each_document_once_per_batch(tmp_path, monkeypatch, available):
    scope = KnowledgeScope(tenant_id="tenant")
    docs = InMemoryDocStore()
    original = DocumentRecord("doc", "terms", "First clause. Second clause.", tenant_id="tenant")
    original.chunks = [
        ChunkRecord("c1", "doc", "First clause.", 0),
        ChunkRecord("c2", "doc", "Second clause.", 1),
    ]
    if available:
        docs.save(original)
    service = SimpleNamespace(bundle=SimpleNamespace(docs=docs))
    archive = SourceStore(tmp_path / "sources.sqlite3")
    monkeypatch.setattr(evidence_sources, "source_store", lambda svc: archive)
    reads = []
    get = docs.get

    def counted(doc_id, *, tenant_id=None):
        reads.append((doc_id, tenant_id))
        return get(doc_id, tenant_id=tenant_id)

    monkeypatch.setattr(docs, "get", counted)
    entries = [
        {"id": f"hit-{i}", "citations": [{"doc_id": "doc", "chunk_id": chunk}]}
        for i, chunk in enumerate(["c1", "c2", "c1", "c2"])
    ]
    rows = evidence_sources.publish_evidence_batch(service, entries, scope=scope)
    assert reads == [("doc", "tenant")]
    assert all(bool(row["source_refs"]) is available for row in rows)
    if available:
        assert rows[0]["source_refs"][0]["chunk_id"] == "c1"
        assert rows[1]["source_refs"][0]["chunk_id"] == "c2"

    # Both successful and missing reads expire at the end of this batch.
    reads.clear()
    if available:
        docs.delete("doc", tenant_id="tenant")
    else:
        docs.save(original)
    refreshed = evidence_sources.publish_evidence_batch(service, entries, scope=scope)
    assert reads == [("doc", "tenant")]
    assert all(bool(row["source_refs"]) is not available for row in refreshed)
