"""Graph candidates surface edge → chunk provenance (ARCH_OPTIMIZATION_V2 O-6)."""

from __future__ import annotations

from agentic_graphrag.core.ports.stores import EntityRecord, RelationRecord
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.retrieval.provenance import source_chunk_ids, source_citations
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore


def _rel(**kw) -> RelationRecord:
    base = dict(id="r1", type="PARENT_OF", head_id="e1", tail_id="e2")
    return RelationRecord(**{**base, **kw})


def test_source_citations_dedupes_and_skips_empty() -> None:
    rel = _rel(
        sources=[
            {"doc_id": "d1", "chunk_id": "c1", "span": "Apex owns NovaTech"},
            {"doc_id": "d1", "chunk_id": "c1"},
            {"doc_id": "d2", "chunk_id": ""},
        ]
    )
    cites = source_citations([rel, rel])
    assert [(c.doc_id, c.chunk_id, c.relation_id) for c in cites] == [("d1", "c1", "r1")]
    assert source_chunk_ids(cites) == ["c1"]
    assert source_citations([_rel()]) == []


def test_neighbor_and_path_candidates_carry_chunk_ids() -> None:
    store = InMemoryGraphStore()
    store.upsert_entities(
        [
            EntityRecord(id="e1", name="Apex Holdings", type="Company"),
            EntityRecord(id="e2", name="NovaTech Industries", type="Company"),
        ]
    )
    store.upsert_relations(
        [
            _rel(
                head_name="Apex Holdings",
                tail_name="NovaTech Industries",
                sources=[{"doc_id": "doc_01", "chunk_id": "doc_01:0", "span": "parent of"}],
            )
        ]
    )
    ret = GraphRetriever(store)
    nbrs = ret.neighbors("Apex Holdings", sub_question="subsidiary of Apex Holdings")
    assert nbrs and nbrs[0].structured["source_chunk_ids"] == ["doc_01:0"]
    assert any(c.chunk_id == "doc_01:0" for c in nbrs[0].citations)
    paths = ret.paths("Apex Holdings", "NovaTech Industries")
    assert paths and paths[0].structured["source_chunk_ids"] == ["doc_01:0"]
