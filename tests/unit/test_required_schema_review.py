"""A required review policy keeps candidate facts out of all graph reads."""

from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord
from agentic_graphrag.knowledge.graph_ingest import GraphIngestDeps, GraphIngestor
from agentic_graphrag.knowledge.review.executor import ReviewExecutor, ReviewExecutorDeps
from agentic_graphrag.knowledge.review.queue import ReviewQueue
from agentic_graphrag.stores.doc_store import InMemoryDocStore
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore


def test_required_fact_is_only_published_after_approval_and_reject_is_non_destructive(tmp_path):
    graph, queue = InMemoryGraphStore(), ReviewQueue(tmp_path / "review.jsonl")
    kb = KnowledgeBase(tenant_id="t", review_policy="required")
    ingest = GraphIngestor(GraphIngestDeps(graph=graph, knowledge_base=kb, review_queue=queue))
    chunk = ChunkRecord(
        chunk_id="c",
        doc_id="d",
        text="Alice Stone is the CEO of Apex Holdings.",
        index=0,
        tenant_id="t",
        metadata={"document_version": 1},
    )
    result = ingest.ingest_chunks([chunk], tenant_id="t", batch_id="batch")
    assert result.conflicts_review == 1 and result.accepted == 0
    assert graph.counts()["nodes"] == 0
    ingest.ingest_chunks([chunk], tenant_id="t", batch_id="batch")
    assert len(queue.list(tenant_id="t")) == 1
    item = queue.list(tenant_id="t")[0]
    docs = InMemoryDocStore()
    docs.save(DocumentRecord(doc_id="d", title="D", content=chunk.text, tenant_id="t"))
    executor = ReviewExecutor(ReviewExecutorDeps(graph=graph, review_queue=queue, docs=docs))
    decided = queue.decide(item.id, "approve", tenant_id="t", reviewer="human")
    assert executor.execute(decided)["applied"]
    assert len(graph.list_relations()) == 1
    assert executor.execute(decided)["applied"]
    assert len(graph.list_relations()) == 1


def test_stale_required_review_cannot_resurrect_a_deleted_document(tmp_path):
    graph, queue = InMemoryGraphStore(), ReviewQueue(tmp_path / "review.jsonl")
    kb = KnowledgeBase(tenant_id="t", review_policy="required")
    ingest = GraphIngestor(GraphIngestDeps(graph=graph, knowledge_base=kb, review_queue=queue))
    chunk = ChunkRecord(
        chunk_id="c",
        doc_id="d",
        text="Alice Stone is the CEO of Apex Holdings.",
        index=0,
        tenant_id="t",
        metadata={"document_version": 1},
    )
    ingest.ingest_chunks([chunk], tenant_id="t")
    item = queue.decide(queue.list(tenant_id="t")[0].id, "approve", tenant_id="t")
    executor = ReviewExecutor(ReviewExecutorDeps(graph=graph, docs=InMemoryDocStore()))
    assert executor.execute(item)["effect"] == "stale_document"
    assert graph.counts()["nodes"] == 0
