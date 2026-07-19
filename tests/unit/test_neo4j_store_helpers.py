"""Unit tests for neo4j_store relation serialization / direction rebuild (no live Neo4j)."""

from __future__ import annotations

from agentic_graphrag.stores.neo4j_store import (
    _attrs_for_neo4j,
    _attrs_from_neo4j,
    _rel_to_record,
    _sources_for_neo4j,
    _sources_from_neo4j,
)


class _FakeRel:
    """Minimal stand-in for a neo4j Relationship (``dict(rel)`` + ``.type``)."""

    def __init__(self, type_: str, props: dict) -> None:
        self.type = type_
        self._props = props

    def __iter__(self):
        return iter(self._props.items())


def test_sources_roundtrip_json():
    src = [{"doc_id": "d1", "chunk_id": "c1", "span": "x", "confidence": 0.9}]
    raw = _sources_for_neo4j(src)
    assert isinstance(raw, str)
    back = _sources_from_neo4j(raw)
    assert back == src


def test_attrs_roundtrip_json():
    attrs = {"source_doc_id": "d1", "k": "v"}
    assert _attrs_from_neo4j(_attrs_for_neo4j(attrs)) == attrs


def test_rel_to_record_prefers_stored_direction_over_walk():
    """Undirected walk may reverse orientation; stored head/tail win."""
    rel = _FakeRel(
        "CEO_OF",
        {
            "id": "r1",
            "head_id": "h1",
            "tail_id": "t1",
            "head_name": "Elena Varga",
            "tail_name": "Apex Holdings",
            "confidence": 0.95,
            "attributes": "{}",
            "sources": '[{"doc_id": "d1"}]',
        },
    )
    rec = _rel_to_record(
        rel,
        walk_from_name="Apex Holdings",  # reverse walk
        walk_other_name="Elena Varga",
    )
    assert rec.head_name == "Elena Varga"
    assert rec.tail_name == "Apex Holdings"
    assert rec.head_id == "h1"
    assert rec.tail_id == "t1"
    assert rec.sources == [{"doc_id": "d1"}]
    assert rec.confidence == 0.95


def test_rel_to_record_legacy_falls_back_to_walk():
    rel = _FakeRel("PARENT_OF", {"id": "legacy", "confidence": 1.0})
    rec = _rel_to_record(
        rel,
        walk_from_name="Apex",
        walk_other_name="NovaTech",
    )
    assert rec.head_name == "Apex"
    assert rec.tail_name == "NovaTech"
    assert rec.sources == []
