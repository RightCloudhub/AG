"""Degradation attribution where the index pipeline and model-pinned vector search meet."""

import logging

import pytest
from test_ingest_index_pipeline import setup_pipeline, upload

from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.knowledge.graph_ingest import GraphIngestDeps, GraphIngestor
from agentic_graphrag.knowledge.index_config import IngestWorkerConfig
from agentic_graphrag.knowledge.ingest_worker import IngestWorker
from agentic_graphrag.observability.degradation import (
    REASON_EMBEDDING_UNAVAILABLE,
    REASON_EXTRACT_FAILED,
    REASON_INDEX_WRITE_FAILED,
    REASON_VECTOR_STORE_UNAVAILABLE,
    ChannelHealth,
    ChannelStatus,
)
from agentic_graphrag.retrieval.vector import VectorRetriever
from agentic_graphrag.stores.doc_store import FileDocStore
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore


def degradations(caplog, component):
    return [r for r in caplog.records if getattr(r, "component", None) == component]


class PinnedStore:
    """A model-pinned index: it chooses the model and embeds through the callback."""

    def __init__(self, *, fail=False):
        self.fail = fail

    def search(self, vector, top_k=10, *, tenant_id=None):
        raise AssertionError("model-pinned stores are searched with search_with_model")

    def search_with_model(self, query, embed, *, top_k, tenant_id):
        vector = embed(query, "pinned-model")
        if self.fail:
            raise ConnectionError("qdrant unreachable")
        chunk = ChunkRecord("c1", "d1", "alpha text", 0, tenant_id=tenant_id, embedding=vector)
        return [(chunk, 1.0)]


class PinnedEmbedder:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.models = []

    def embed(self, text):
        raise AssertionError("pinned search must use embed_for_model")

    def embed_for_model(self, text, model):
        self.models.append(model)
        if self.fail:
            raise PermissionError("401 from the embedding gateway")
        return [1.0, 0.0]


def test_pinned_search_embeds_with_the_index_model():
    embedder = PinnedEmbedder()
    hits = VectorRetriever(PinnedStore(), embedder).search("alpha", tenant_id="alpha")
    assert [c.content for c in hits] == ["alpha text"] and embedder.models == ["pinned-model"]


def test_pinned_search_embedder_failure_is_attributed_once(caplog):
    health = ChannelHealth()
    retriever = VectorRetriever(PinnedStore(), PinnedEmbedder(fail=True), health=health)
    with caplog.at_level(logging.WARNING), pytest.raises(PermissionError):
        retriever.search("alpha", tenant_id="alpha")
    assert len(degradations(caplog, "embedding")) == 1
    assert degradations(caplog, "vector") == []
    channels = {row["channel"]: row for row in health.degraded_channels()}
    assert channels["embedding"]["reason"] == REASON_EMBEDDING_UNAVAILABLE
    assert channels["vector"] == {
        "channel": "vector",
        "reason": REASON_EMBEDDING_UNAVAILABLE,
        "status": ChannelStatus.FAILED,
    }


def test_pinned_search_store_failure_is_attributed_to_the_store(caplog):
    health = ChannelHealth()
    retriever = VectorRetriever(PinnedStore(fail=True), PinnedEmbedder(), health=health)
    with caplog.at_level(logging.WARNING), pytest.raises(ConnectionError):
        retriever.search("alpha", tenant_id="alpha")
    assert degradations(caplog, "embedding") == []
    assert [r.reason for r in degradations(caplog, "vector")] == [REASON_VECTOR_STORE_UNAVAILABLE]
    assert [row["channel"] for row in health.degraded_channels()] == ["vector"]


def test_chunking_error_fails_the_document_and_is_kept_on_it(tmp_path):
    docs = FileDocStore(tmp_path)
    worker = IngestWorker(
        doc_store=docs,
        vector_store=InMemoryVectorStore(),
        embed_many=lambda texts: [[1.0, 0.0] for _ in texts],
        config=IngestWorkerConfig(chunk_size_tokens=2, chunk_overlap_tokens=0),
    )
    row = upload(worker.pipeline.lifecycle, "evidence")
    result = worker.process_doc(row["doc_id"], tenant_id="alpha")
    assert result.fatal and "chunk_size_tokens" in (result.error or "")
    doc = docs.get(row["doc_id"], tenant_id="alpha")
    assert doc.metadata["index_error"] == result.error
    assert not doc.metadata.get("chunks_initialized") and doc.chunks == []


def test_embedding_failure_is_recorded_and_leaves_the_task_partial(tmp_path, caplog):
    def broken(texts):
        raise ValueError("embedder returned garbage")

    lifecycle, worker, tasks, _ = setup_pipeline(tmp_path, embed=broken)
    row = upload(lifecycle)
    task = tasks.create("alpha", [row])
    with caplog.at_level(logging.WARNING):
        worker.run_once()
    records = degradations(caplog, "embedding")
    assert records and {r.reason for r in records} == {REASON_EMBEDDING_UNAVAILABLE}
    assert all(r.doc_id == row["doc_id"] and r.chunks == 2 for r in records)
    assert tasks.get(task.id).status == "partial"


def test_vector_and_fulltext_write_failures_are_recorded(tmp_path, caplog, monkeypatch):
    lifecycle, worker, _, _ = setup_pipeline(tmp_path)
    row = upload(lifecycle)
    monkeypatch.setattr(worker.vector_store, "upsert", lambda chunks: 0)
    monkeypatch.setattr(worker.fulltext_store, "index", lambda chunks: 0)
    with caplog.at_level(logging.WARNING):
        result = worker.process_doc(row["doc_id"], tenant_id="alpha")
    assert result.error and not result.fatal
    for component in ("vector_index", "fulltext_index"):
        records = degradations(caplog, component)
        assert records and {r.reason for r in records} == {REASON_INDEX_WRITE_FAILED}
        assert all(r.doc_id == row["doc_id"] for r in records)


def test_graph_extraction_failure_is_recorded_per_chunk(tmp_path, caplog, monkeypatch):
    graph = GraphIngestor(GraphIngestDeps(graph=InMemoryGraphStore()))
    worker = IngestWorker(
        doc_store=FileDocStore(tmp_path),
        vector_store=InMemoryVectorStore(),
        fulltext_store=BM25FulltextStore(),
        graph_ingestor=graph,
        config=IngestWorkerConfig(chunk_size_tokens=8, chunk_overlap_tokens=0),
    )
    row = upload(worker.pipeline.lifecycle)

    def explode(chunks, **kwargs):
        raise RuntimeError("extractor down")

    monkeypatch.setattr(graph, "ingest_chunks", explode)
    with caplog.at_level(logging.WARNING):
        result = worker.process_doc(row["doc_id"], tenant_id="alpha")
    records = degradations(caplog, "graph_ingest")
    assert len(records) == result.chunks_created == 4
    assert {r.reason for r in records} == {REASON_EXTRACT_FAILED}
    assert {r.severity for r in records} == {ChannelStatus.FAILED}
    assert result.error == "graph_failed: RuntimeError"
