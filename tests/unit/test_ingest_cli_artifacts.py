"""Tenant ownership and model metadata survive the CLI artifact boundary."""

import json
import uuid
from types import SimpleNamespace

import httpx

from agentic_graphrag.cli.index_batches import embed_chunks
from agentic_graphrag.config import AppConfig
from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.knowledge.index_config import IngestWorkerConfig
from agentic_graphrag.knowledge.ingest_worker import IngestWorker
from agentic_graphrag.stores.doc_store import FileDocStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore


def test_cli_ingest_and_embedding_snapshot_keep_tenant(tmp_path, monkeypatch):
    from agentic_graphrag.cli import cases_run, index_cmd, ingest
    from agentic_graphrag.stores.factory import _try_load_embeddings

    cfg = AppConfig()
    for field in type(cfg.paths).model_fields:
        if field.endswith("_dir"):
            setattr(cfg.paths, field, str(tmp_path / field))
    raw = tmp_path / "raw_docs_dir"
    raw.mkdir()
    (raw / "source.txt").write_text("original text")
    docs = FileDocStore(tmp_path / "docs")
    worker = IngestWorker(doc_store=docs, vector_store=InMemoryVectorStore())
    monkeypatch.setenv("AGR_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(ingest, "get_config", lambda: cfg)
    monkeypatch.setattr(ingest, "build_cli_worker", lambda: worker)
    path = tmp_path / "chunks.jsonl"
    ingest.ingest_main(["--input", str(raw), "--out", str(path), "--tenant", "alpha"])
    row = json.loads(path.read_text())
    uuid.UUID(row["doc_id"])
    assert row["tenant_id"] == "alpha" and row["text"] == "original text"
    chunk = ChunkRecord(**row)
    chunk.embedding = [1.0]
    chunk.embedding_model = "test-model"
    index_cmd._persist_memory_vectors([chunk], cfg)
    for vector in (InMemoryVectorStore(), cases_run._offline_vector(cfg).store):
        _try_load_embeddings(vector, cfg)
        assert len(vector.search([1.0], tenant_id="alpha")) == 1
        assert not vector.search([1.0], tenant_id="default")
        assert vector.search([1.0], tenant_id="alpha")[0][0].embedding_model == "test-model"


def test_cli_embedding_batches_retry_and_keep_tenants_separate():
    chunks = [
        ChunkRecord(str(i), "doc", str(i), i, tenant_id=tenant)
        for i, tenant in enumerate(["alpha", "beta", "alpha"])
    ]
    calls = []

    def embed(texts):
        calls.append(texts)
        if len(calls) <= 2:
            raise httpx.ReadTimeout("retry")
        return [[1.0] for _ in texts]

    embed_chunks(
        chunks,
        embed,
        config=IngestWorkerConfig(
            embedding_batch_size=2, retry_base_seconds=0, embedding_model="model"
        ),
    )
    assert calls == [["0", "2"], ["0", "2"], ["0", "2"], ["1"]]
    assert all(c.embedding_model == "model" for c in chunks)


def test_cli_graph_provenance_uses_chunk_ownership():
    from agentic_graphrag.cli.graph_cmd import _stamp_chunk_ownership
    from agentic_graphrag.knowledge.extract_rules import extract_rules_from_chunk

    chunk = ChunkRecord(
        "d:0", "d", "Kenji Watanabe founded SiliconForge in 2009.", 0, tenant_id="alpha"
    )
    triple = extract_rules_from_chunk(chunk)[0]
    triple.source_doc_id = "forged"
    triple.attributes["tenant_id"] = "beta"
    result = SimpleNamespace(chunk_id=chunk.chunk_id, doc_id=chunk.doc_id, accepted=[triple])
    _stamp_chunk_ownership(SimpleNamespace(chunk_results=[result]), [chunk])
    assert triple.source_doc_id == "d" and triple.attributes["tenant_id"] == "alpha"
