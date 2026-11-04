"""Retention of document-operation records and retained sources (K-06)."""

import importlib.util
import time
from pathlib import Path

from agentic_graphrag.config_enterprise import RetentionConfig
from agentic_graphrag.core.ports.document_ops import (
    ChunkPatch,
    Command,
    CommandScope,
    DocOperation,
    SourceArtifact,
    payload_hash,
)
from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord
from agentic_graphrag.stores.doc_retention import prune_doc_store
from agentic_graphrag.stores.doc_store import FileDocStore

FUTURE = time.time() + 400 * 86_400


def seeded(tmp_path):
    store = FileDocStore(tmp_path / "docs")
    chunk = ChunkRecord("d:0", "d", "text", 0, metadata={"document_version": 2}, tenant_id="a")
    store.save(DocumentRecord("d", "Doc", "text", tenant_id="a", version=2, chunks=[chunk]))
    blobs = {name: store.artifacts.put("a", name.encode()) for name in ("old", "cur", "orphan")}
    blobs["pending"] = store.artifacts.put("a", b"pending")
    store.record_source(("a", "d", 1), SourceArtifact(blobs["old"]))
    store.record_source(("a", "d", 2), SourceArtifact(blobs["cur"]))
    staged = DocOperation("parse-op", "a", "new", "parse", "parse-op")
    staged.payload["files"] = [{"doc_id": "new", "sha256": blobs["pending"]}]
    store.stage_parse(staged, [{"doc_id": "new", "title": "n", "source": "s"}])
    for key, state in (("done", "done"), ("live", "pending")):
        op = DocOperation(f"op-{key}", "a", "d", "chunk_patch", f"t-{key}", {"chunk_id": "d:0"})
        patch = ChunkPatch("a", "d", "d:0", store.get("d", tenant_id="a").chunks[0].revision)
        scope = CommandScope("a", "chunk_patch", "d/d:0", key)
        store.apply_chunk_patch(
            patch, Command(scope, payload_hash({"k": key}), op), lambda c, o: {"ok": True}
        )
        store.update_op(f"op-{key}", state=state)
    store.stage_content("op-done", ("a", "d"), ("staged text", {}))
    store.save(DocumentRecord("gone", "Gone", "x", tenant_id="a"))
    store.delete("gone", tenant_id="a")
    return store, blobs


def table_count(store, table, where="1=1"):
    with store._connect() as db:
        return db.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


def test_retention_keeps_current_and_pending_references(tmp_path):
    store, blobs = seeded(tmp_path)
    retention = RetentionConfig()
    preview = prune_doc_store(store, retention, now=FUTURE, dry_run=True)
    assert preview["operations"] == 1 and preview["source_blobs"] == 2
    assert store.artifacts.exists("a", blobs["orphan"]) and table_count(store, "ops") == 3
    counts = prune_doc_store(store, retention, now=FUTURE)
    assert counts == {**preview}
    assert not store.artifacts.exists("a", blobs["old"])
    assert not store.artifacts.exists("a", blobs["orphan"])
    assert store.artifacts.exists("a", blobs["cur"]) and store.artifacts.exists(
        "a", blobs["pending"]
    )
    assert store.source_for(("a", "d", 1)) is None and store.source_for(("a", "d", 2))
    assert store.get_op("op-done") is None and store.staged_content("op-done") is None
    assert store.get_op("op-live") is not None and store.get_op("parse-op") is not None
    assert table_count(store, "idempotency") == 1  # the pending command's key never expires
    current = store.get("d", tenant_id="a").chunks[0].revision
    assert table_count(store, "chunk_history") == 1
    assert table_count(store, "chunk_history", f"revision={current}") == 1
    assert table_count(store, "tombstones") == 0
    assert prune_doc_store(store, retention, now=time.time())["source_blobs"] == 0


def test_prune_script_reports_document_retention(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGR_RUNTIME_DIR", str(tmp_path))
    seeded(tmp_path)
    path = Path(__file__).resolve().parents[2] / "scripts" / "prune_data_files.py"
    spec = importlib.util.spec_from_file_location("prune_data_files", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main(["--dry-run"])
    out = capsys.readouterr().out
    assert "documents:" in out and "operations=0" in out and "dry_run=True" in out
