"""BL-01 / BL-03 / BL-14: runtime write path — upload → graph → review → graph."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.api.service_ingest import build_inline_worker
from agentic_graphrag.knowledge.extract_rules import extract_rules_from_chunk, sentence_dates
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph, triples_to_records
from agentic_graphrag.knowledge.graph_ingest import GraphIngestDeps, GraphIngestor
from agentic_graphrag.knowledge.incremental import IncrementalUpdater
from agentic_graphrag.knowledge.ingest_tasks import IngestStatus, IngestTaskStore
from agentic_graphrag.knowledge.ingest_worker import IngestWorker
from agentic_graphrag.knowledge.review.executor import ReviewExecutor, ReviewExecutorDeps
from agentic_graphrag.knowledge.review.queue import ReviewDecision, ReviewQueue, ReviewType
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
from agentic_graphrag.knowledge.temporal import (
    is_current,
    prefer_incoming,
    temporal_weight,
    time_key,
    validity_label,
)
from agentic_graphrag.retrieval.graph_relation import edge_score
from agentic_graphrag.stores.doc_store import InMemoryDocStore
from agentic_graphrag.stores.interfaces import ChunkRecord, DocumentRecord, RelationRecord
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore


def _t(head: tuple[str, str], rel: str, tail: tuple[str, str], **kw) -> Triple:
    return Triple(
        head=EntityMention(name=head[0], type=head[1]),
        relation=rel,
        tail=EntityMention(name=tail[0], type=tail[1]),
        confidence=kw.pop("confidence", 0.9),
        **kw,
    )


def _ceo(person: str, company: str, start: str | None = None, conf: float = 0.9) -> Triple:
    attrs = {"valid_from": start} if start else {}
    return _t((person, "Person"), "CEO_OF", (company, "Company"), confidence=conf, attributes=attrs)


def _relations(store: InMemoryGraphStore) -> list[RelationRecord]:
    return list(store._relations.values())  # noqa: SLF001 — test introspection


# --- BL-14 temporal model -------------------------------------------------


def test_time_keys_and_currency() -> None:
    assert time_key("2019") == (2019, 1, 1)
    assert time_key("2024-05") == (2024, 5, 1)
    assert time_key("soon") is None
    assert is_current({}) and not is_current({"valid_to": "2016"})
    assert validity_label({"valid_from": "2010", "valid_to": "2016"}) == " [2010–2016]"
    assert prefer_incoming(({"valid_from": "2025"}, 0.6), ({"valid_from": "2019"}, 0.99))


def test_same_edge_merge_prefers_later_date_over_confidence() -> None:
    old = _ceo("Elena Varga", "Apex Holdings", "2019", conf=0.99)
    new = _ceo("Elena Varga", "Apex Holdings", "2021", conf=0.7)
    _e, rels = triples_to_records([old, new])
    assert len(rels) == 1 and rels[0].attributes["valid_from"] == "2021"
    assert len(rels[0].sources) == 2


def test_dated_successor_closes_previous_ceo_instead_of_deleting() -> None:
    store = InMemoryGraphStore()
    load_triples_into_graph(store, [_ceo("Elena Varga", "Apex Holdings", "2019", conf=0.99)])
    result = IncrementalUpdater(store).apply_batch(
        [_ceo("Ravi Shah", "Apex Holdings", "2025", 0.7)]
    )
    assert result.conflicts_temporal == 1
    by_person = {r.head_name: r for r in _relations(store)}
    assert by_person["Elena Varga"].attributes["valid_to"] == "2025"
    assert is_current(by_person["Ravi Shah"].attributes)


def test_older_fact_is_stored_as_history() -> None:
    store = InMemoryGraphStore()
    load_triples_into_graph(store, [_ceo("Ravi Shah", "Apex Holdings", "2025")])
    IncrementalUpdater(store).apply_batch([_ceo("Elena Varga", "Apex Holdings", "2019", 0.99)])
    by_person = {r.head_name: r for r in _relations(store)}
    assert by_person["Elena Varga"].attributes["valid_to"] == "2025"
    assert "valid_to" not in by_person["Ravi Shah"].attributes


def test_historical_edges_rank_below_current_unless_asked() -> None:
    old = RelationRecord("r1", "CEO_OF", "a", "b", "X", "Y", 0.9, {"valid_to": "2016"})
    cur = RelationRecord("r2", "CEO_OF", "c", "b", "Z", "Y", 0.9, {"valid_from": "2016"})
    q = "Who is the CEO of Y?"
    assert edge_score(old, q) < edge_score(cur, q)
    assert temporal_weight(old.attributes, "Who was previously CEO of Y?") == 1.0


# --- cardinality + tenancy ---------------------------------------------------


def test_multi_valued_relation_never_conflicts() -> None:
    store = InMemoryGraphStore()
    prod = _t(("NovaTech", "Company"), "PRODUCES", ("Q1", "Product"))
    load_triples_into_graph(store, [prod])
    result = IncrementalUpdater(store).apply_batch(
        [_t(("NovaTech", "Company"), "PRODUCES", ("Q2", "Product"), confidence=0.99)]
    )
    assert result.conflicts_auto == result.conflicts_review == result.conflicts_kept == 0
    assert {r.tail_name for r in _relations(store)} == {"Q1", "Q2"}


def test_tail_functional_conflict_detects_new_ceo_of_same_company() -> None:
    store = InMemoryGraphStore()
    load_triples_into_graph(store, [_ceo("Elena Varga", "Apex Holdings", conf=0.6)])
    result = IncrementalUpdater(store).apply_batch([_ceo("Ravi Shah", "Apex Holdings", conf=0.95)])
    assert result.conflicts_auto == 1
    assert {r.head_name for r in _relations(store)} == {"Ravi Shah"}


def test_tenant_batch_never_retires_shared_edges() -> None:
    store = InMemoryGraphStore()
    load_triples_into_graph(store, [_ceo("Elena Varga", "Apex Holdings", conf=0.6)])
    updater = IncrementalUpdater(store, tenant_id="acme")
    result = updater.apply_batch([_ceo("Ravi Shah", "Apex Holdings", conf=0.95)])
    assert result.conflicts_auto == 0
    tenants = {(r.head_name, r.tenant_id) for r in _relations(store)}
    assert tenants == {("Elena Varga", ""), ("Ravi Shah", "acme")}


# --- BL-01 extraction + graph stage -----------------------------------------


def _chunk(text: str, doc_id: str = "d1") -> ChunkRecord:
    return ChunkRecord(chunk_id=f"{doc_id}:0", doc_id=doc_id, text=text, index=0)


def test_rule_extractor_covers_english_and_chinese_patterns() -> None:
    text = (
        "Orion Systems is a cloud software company headquartered in Seattle. "
        "The CEO of Orion Systems is Grace Liu. "
        "Marcus Chen worked at Orion Systems from 2010 to 2016. "
        "NovaTech Industries produces QuantumEdge Server and the EdgeLite Workstation.\n"
        "星辰科技的首席执行官是王伟。"
    )
    got = {(t.head.name, t.relation, t.tail.name) for t in extract_rules_from_chunk(_chunk(text))}
    assert ("Orion Systems", "LOCATED_IN", "Seattle") in got
    assert ("Grace Liu", "CEO_OF", "Orion Systems") in got
    assert ("Marcus Chen", "WORKED_AT", "Orion Systems") in got
    assert ("NovaTech Industries", "PRODUCES", "EdgeLite Workstation") in got
    assert ("王伟", "CEO_OF", "星辰科技") in got
    assert sentence_dates("He worked there from 2010 to 2016.") == {
        "valid_from": "2010",
        "valid_to": "2016",
    }


def _worker(store: InMemoryGraphStore, queue: ReviewQueue, tasks: IngestTaskStore) -> IngestWorker:
    return IngestWorker(
        doc_store=InMemoryDocStore(),
        vector_store=InMemoryVectorStore(),
        task_store=tasks,
        graph_ingestor=GraphIngestor(GraphIngestDeps(graph=store, review_queue=queue)),
    )


def _queue_doc(worker: IngestWorker, tasks: IngestTaskStore, text: str) -> str:
    doc = DocumentRecord(doc_id="news.md", title="news", content=text, tenant_id="t1")
    worker.doc_store.save(doc)
    return tasks.create("t1", [{"doc_id": "news.md"}]).id


def test_worker_writes_uploaded_facts_into_graph() -> None:
    store, queue, tasks = InMemoryGraphStore(), ReviewQueue(), IngestTaskStore()
    worker = _worker(store, queue, tasks)
    task_id = _queue_doc(worker, tasks, "Kenji Watanabe founded SiliconForge in 2009.")
    results = worker.run_once()
    assert results[0].graph["accepted"] == 1
    rel = _relations(store)[0]
    assert (rel.head_name, rel.type, rel.tenant_id) == ("Kenji Watanabe", "FOUNDED", "t1")
    assert rel.sources[0]["doc_id"] == "news.md"
    assert tasks.get(task_id).status == IngestStatus.DONE.value


def test_conflict_goes_to_review_and_approval_takes_effect() -> None:
    store, queue, tasks = InMemoryGraphStore(), ReviewQueue(), IngestTaskStore()
    seed = _ceo("Elena Varga", "Apex Holdings", conf=0.8)
    seed.attributes = {"tenant_id": "t1"}
    load_triples_into_graph(store, [seed])
    worker = _worker(store, queue, tasks)
    task_id = _queue_doc(worker, tasks, "The CEO of Apex Holdings is Ravi Shah.")
    worker.run_once()
    assert tasks.get(task_id).status == IngestStatus.REVIEW.value  # REVIEW now has a producer
    item = queue.list(type=ReviewType.CONFLICT.value)[0]
    assert item.batch_id == task_id and item.tenant_id == "t1"

    executor = ReviewExecutor(ReviewExecutorDeps(graph=store, review_queue=queue, task_store=tasks))
    decided = queue.decide(item.id, ReviewDecision.APPROVE, tenant_id="t1")
    outcome = executor.execute(decided)
    assert outcome["effect"] == "fact_replaced" and outcome["retired"] == 1
    assert {r.head_name for r in _relations(store)} == {"Ravi Shah"}
    assert queue.get(item.id).execution["applied"] is True
    assert tasks.get(task_id).status == IngestStatus.DONE.value
    assert executor.execute(decided) == outcome  # idempotent replay


def test_rejected_spotcheck_rolls_back_upload_facts() -> None:
    store, queue = InMemoryGraphStore(), ReviewQueue()
    ingestor = GraphIngestor(GraphIngestDeps(graph=store, review_queue=queue))
    ingestor.ingest_chunks(
        [_chunk("Lee Min-jun founded Harbor Components.", "up.md")], tenant_id="t1"
    )
    assert len(_relations(store)) == 1
    item = queue.enqueue(ReviewType.SPOTCHECK, {"doc_ids": ["up.md"]}, tenant_id="t1")
    decided = queue.decide(item.id, ReviewDecision.REJECT, tenant_id="t1")
    outcome = ReviewExecutor(ReviewExecutorDeps(graph=store, review_queue=queue)).execute(decided)
    assert outcome["effect"] == "upload_rolled_back"
    assert _relations(store) == []


def test_api_upload_reaches_graph_via_inline_worker(monkeypatch) -> None:
    service = QueryService.create_offline()
    service.bundle.docs = InMemoryDocStore()
    service.ingest_tasks = IngestTaskStore()
    service.review_queue = ReviewQueue()
    client = TestClient(create_app(query_service=service))
    body = "Nimbus Robotics is headquartered in Osaka. The CEO of Nimbus Robotics is Aiko Mori."
    up = client.post("/v1/docs", files={"files": ("nimbus.md", body.encode(), "text/markdown")})
    assert up.status_code == 200
    build_inline_worker(service).run_once()
    task = client.get(f"/v1/ingest-tasks/{up.json()['data']['id']}").json()["data"]
    assert task["status"] == "done" and "graph fact" in task["message"]
    found = client.get("/v1/graph/entities", params={"q": "Nimbus"}).json()["data"]
    assert any(e["name"] == "Nimbus Robotics" for e in found)
    assert "Aiko Mori" in service.known_entities  # query side sees new names
    service.close()


def test_extraction_and_resolution_decisions_take_effect() -> None:
    store, queue = InMemoryGraphStore(), ReviewQueue()
    executor = ReviewExecutor(ReviewExecutorDeps(graph=store, review_queue=queue))
    triple = _t(("Acme", "Company"), "ACQUIRED", ("Beta", "Company")).model_dump(mode="json")
    item = queue.enqueue(ReviewType.EXTRACTION, {"triple": triple})
    assert executor.execute(queue.decide(item.id, ReviewDecision.APPROVE))["written"] == 1
    again = queue.enqueue(ReviewType.EXTRACTION, {"triple": triple})
    assert executor.execute(queue.decide(again.id, ReviewDecision.REJECT))["retired"] == 1
    assert _relations(store) == []

    load_triples_into_graph(store, [_ceo("Elena Varga", "Apex Holdings"), _ceo("E. Varga", "Apex")])
    merge = queue.enqueue(ReviewType.RESOLUTION, {"keep": "Elena Varga", "drop": "E. Varga"})
    out = executor.execute(queue.decide(merge.id, ReviewDecision.APPROVE))
    assert out["effect"] == "entities_merged"
    skipped = queue.enqueue(ReviewType.FEEDBACK, {})
    assert executor.execute(queue.decide(skipped.id, ReviewDecision.APPROVE))["applied"] is False


def test_cli_worker_is_wired_to_queue_and_graph() -> None:
    from agentic_graphrag.knowledge.ingest_worker_cli import build_cli_worker

    worker = build_cli_worker()
    assert worker.task_store is not None and worker.graph_ingestor is not None
