"""BL-01: runtime upload → graph path (GraphIngestPipeline + worker wiring)."""

from __future__ import annotations

import json

from agentic_graphrag.knowledge.graph_builder import triples_to_records
from agentic_graphrag.knowledge.graph_ingest import (
    LLM_UNAVAILABLE,
    GraphIngestPipeline,
)
from agentic_graphrag.knowledge.incremental import IncrementalUpdater
from agentic_graphrag.knowledge.ingest_tasks import IngestStatus, IngestTaskStore
from agentic_graphrag.knowledge.ingest_worker import IngestWorker, IngestWorkerConfig
from agentic_graphrag.knowledge.ingest_worker_cli import build_cli_worker
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
from agentic_graphrag.llm.provider import MockLLMProvider
from agentic_graphrag.stores.interfaces import DocumentRecord
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

_TRIPLE_JSON = json.dumps(
    {
        "triples": [
            {
                "head": {"name": "Alice", "type": "Person"},
                "relation": "WORKED_AT",
                "tail": {"name": "Acme", "type": "Company"},
                "confidence": 0.9,
                "source_span": "Alice worked at Acme",
            }
        ]
    }
)

# Slightly higher than the seeded 0.95 rival: inside the review band
# (0.95 < conf < 0.95 + margin) the conflict lands in REVIEW, not auto-update.
_TRIPLE_JSON_REVIEW = json.dumps(
    {
        "triples": [
            {
                "head": {"name": "Alice", "type": "Person"},
                "relation": "WORKED_AT",
                "tail": {"name": "Acme", "type": "Company"},
                "confidence": 0.99,
                "source_span": "Alice worked at Acme",
            }
        ]
    }
)


def _doc(doc_id: str = "doc-1", tenant_id: str = "t1") -> DocumentRecord:
    return DocumentRecord(
        doc_id=doc_id,
        title="Doc",
        content="Alice worked at Acme. Acme competes with Bob Industries.",
        tenant_id=tenant_id,
    )


class _FakeDocStore:
    durable = True

    def __init__(self) -> None:
        self.docs: dict[str, DocumentRecord] = {}

    def save(self, doc: DocumentRecord) -> None:
        self.docs[doc.doc_id] = doc

    def get(self, doc_id: str) -> DocumentRecord | None:
        return self.docs.get(doc_id)

    def list_ids(self, *, tenant_id: str | None = None) -> list[str]:
        return list(self.docs)


class _FakeVector:
    def upsert(self, chunks: list) -> int:
        return len(chunks)


def _seed_relation(head: str, tail: str, conf: float):
    _ents, rels = triples_to_records(
        [
            Triple(
                head=EntityMention(name=head, type="Person"),
                relation="WORKED_AT",
                tail=EntityMention(name=tail, type="Company"),
                confidence=conf,
            )
        ]
    )
    return rels[0]


def _make_worker(
    *,
    store: InMemoryGraphStore,
    llm: MockLLMProvider | None,
    task_store: IngestTaskStore,
    docs: _FakeDocStore,
) -> IngestWorker:
    pipeline = GraphIngestPipeline(
        updater=IncrementalUpdater(store),
        llm=llm,
        chunk_size_chars=1200,
        chunk_overlap_chars=150,
    )
    return IngestWorker(
        doc_store=docs,
        vector_store=_FakeVector(),
        task_store=task_store,
        config=IngestWorkerConfig(),
        graph_pipeline=pipeline,
    )


class TestPipeline:
    def test_without_llm_reports_unavailable(self) -> None:
        store = InMemoryGraphStore()
        pipeline = GraphIngestPipeline(updater=IncrementalUpdater(store), llm=None)
        result = pipeline.process_document(_doc())
        assert result.error == LLM_UNAVAILABLE
        assert store.counts()["relationships"] == 0

    def test_extracts_and_applies_triples(self) -> None:
        store = InMemoryGraphStore()
        llm = MockLLMProvider(responses={"worked at Acme": _TRIPLE_JSON})
        pipeline = GraphIngestPipeline(updater=IncrementalUpdater(store), llm=llm)
        result = pipeline.process_document(_doc())
        assert result.error is None
        assert result.triples_extracted >= 1
        assert result.triples_accepted >= 1
        assert store.counts()["relationships"] >= 1

    def test_conflicts_reach_review_queue(self) -> None:
        from agentic_graphrag.knowledge.review.queue import ReviewQueue, ReviewType

        store = InMemoryGraphStore()
        # Seeded rival edge with different tail (value conflict) at 0.95.
        store.upsert_relations([_seed_relation("Alice", "Zed Corp", 0.95)])
        queue = ReviewQueue()
        updater = IncrementalUpdater(store, review_queue=queue)
        llm = MockLLMProvider(responses={"worked at Acme": _TRIPLE_JSON_REVIEW})
        pipeline = GraphIngestPipeline(updater=updater, llm=llm)
        result = pipeline.process_document(_doc())
        assert result.conflicts_review >= 1
        assert result.review_item_ids, "REVIEW conflicts must enqueue a queue item"
        item = queue.get(result.review_item_ids[0])
        assert item is not None
        assert item.type == ReviewType.CONFLICT.value
        assert item.payload["incoming"]["head"]["name"] == "Alice"


class TestWorkerRouting:
    def test_task_goes_to_review_without_llm(self) -> None:
        store = InMemoryGraphStore()
        tasks = IngestTaskStore()
        docs = _FakeDocStore()
        docs.save(_doc("doc-review"))
        task = tasks.create("t1", [{"doc_id": "doc-review"}])
        worker = _make_worker(store=store, llm=None, task_store=tasks, docs=docs)
        results = worker.run_once()
        assert results, "expected the queued task to be processed"
        assert results[0].graph_result is not None
        assert results[0].graph_result.error == LLM_UNAVAILABLE
        row = tasks.get(task.id)
        assert row.status == IngestStatus.REVIEW.value
        assert "LLM" in row.message

    def test_task_done_with_llm(self) -> None:
        store = InMemoryGraphStore()
        tasks = IngestTaskStore()
        docs = _FakeDocStore()
        docs.save(_doc("doc-ok"))
        task = tasks.create("t1", [{"doc_id": "doc-ok"}])
        llm = MockLLMProvider(responses={"worked at Acme": _TRIPLE_JSON})
        worker = _make_worker(store=store, llm=llm, task_store=tasks, docs=docs)
        results = worker.run_once()
        assert results[0].error is None
        assert results[0].graph_result.triples_accepted >= 1
        assert tasks.get(task.id).status == IngestStatus.DONE.value

    def test_cli_worker_consumes_queue_and_graph(self) -> None:
        worker = build_cli_worker()
        assert worker.task_store is not None
        assert worker.graph_pipeline is not None
