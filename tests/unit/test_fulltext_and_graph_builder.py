from agentic_graphrag.knowledge.graph_builder import triples_to_records
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.interfaces import ChunkRecord


def test_bm25_search():
    store = BM25FulltextStore()
    store.index(
        [
            ChunkRecord(chunk_id="1", doc_id="d", text="Elena Varga is CEO of Apex Holdings", index=0),
            ChunkRecord(chunk_id="2", doc_id="d", text="QuantumEdge Server is a product", index=1),
        ]
    )
    hits = store.search("Elena CEO Apex", top_k=5)
    assert hits
    assert hits[0][0].chunk_id == "1"


def test_triples_to_records_dedupe_entities():
    triples = [
        Triple(
            head=EntityMention(name="Elena Varga", type="Person"),
            relation="CEO_OF",
            tail=EntityMention(name="Apex Holdings", type="Company"),
            confidence=0.9,
        ),
        Triple(
            head=EntityMention(name="Elena Varga", type="Person"),
            relation="WORKED_AT",
            tail=EntityMention(name="Orion Systems", type="Company"),
            confidence=0.8,
        ),
    ]
    entities, relations = triples_to_records(triples)
    assert len(entities) == 3
    assert len(relations) == 2
