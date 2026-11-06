"""Scoped reads use backend lookup capabilities without exposing mutable storage."""

from collections import Counter
from dataclasses import replace

import pytest
from test_knowledge_scope_stores import populate, raw_bundle

from agentic_graphrag.core.knowledge import KnowledgeScope
from agentic_graphrag.core.ports.stores import EntityRecord, RelationRecord
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.retrieval.graph_beam import BeamConfig, BeamExpander, BeamItem
from agentic_graphrag.stores.scoped_bundle import scope_bundle


def test_vocabulary_and_name_lookup_do_not_scan_full_records(monkeypatch):
    raw = raw_bundle()
    bound = scope_bundle(raw, KnowledgeScope(kb_id="one"))
    populate(bound, KnowledgeScope(kb_id="one"))
    foreign = scope_bundle(raw, KnowledgeScope(kb_id="two"))
    foreign.graph.upsert_entities([EntityRecord("secret", "Private", "Person")])

    def no_full_scan(**kwargs):
        raise AssertionError("A name lookup should not load every entity's provenance")

    monkeypatch.setattr(raw.graph, "list_entities", no_full_scan)
    assert set(bound.graph.entity_names(tenant_id="default")) == {"Alice", "Baker"}
    assert bound.graph.get_entity_by_name("Alice").id == "a"
    assert bound.graph.neighbors("Alice")[0][1].name == "Baker"


def test_nested_read_copies_and_alias_ambiguity_remain_isolated():
    raw = raw_bundle()
    bound = scope_bundle(raw, KnowledgeScope(kb_id="one"))
    populate(bound, KnowledgeScope(kb_id="one"))
    alice = bound.graph.get_entity_by_name("Alice")
    alice.aliases = ["A"]
    alice.attributes = {"nested": {"values": ["original"]}}
    bound.graph.upsert_entities([alice])
    assert bound.graph.neighbors("A")[0][1].name == "Baker"
    assert bound.graph.paths("A", "Baker")[0].nodes[0].name == "Alice"
    again = bound.graph.get_entity_by_name("A")
    again.attributes["nested"]["values"].append("changed")
    again.sources[0]["doc_id"] = "changed"
    original = bound.graph.get_entity_by_name("A")
    assert original.attributes["nested"]["values"] == ["original"]
    assert original.sources[0]["doc_id"] == "doc"
    bound.graph.upsert_entities([replace(alice, id="second")])
    assert bound.graph.get_entity_by_name("A") is None
    assert bound.graph.neighbors("A") == []
    with pytest.raises(ValueError, match="Tenant"):
        bound.graph.entity_names(tenant_id="another")


def test_repeated_name_lookup_is_indexed_and_mutations_invalidate_it():
    class CountScans(dict):
        scans = 0

        def values(self):
            self.scans += 1
            return super().values()

    raw = raw_bundle()
    raw.graph._entities = records = CountScans()
    bound = scope_bundle(raw, KnowledgeScope(kb_id="one"))
    populate(bound, KnowledgeScope(kb_id="one"))
    assert bound.graph.get_entity_by_name("Alice").id == "a"
    assert bound.graph.get_entity_by_name("Alice").id == "a"
    assert records.scans == 1
    alice = bound.graph.get_entity_by_name("Alice")
    bound.graph.upsert_entities([replace(alice, name="Renamed", aliases=["New alias"])])
    assert bound.graph.get_entity_by_name("Alice") is None
    assert bound.graph.get_entity_by_name("New alias").id == "a"
    assert records.scans == 2
    bound.graph.delete_entity("a", tenant_id="default")
    assert bound.graph.get_entity_by_name("New alias") is None
    raw.graph.clear()
    assert bound.graph.get_entity_by_name("Baker") is None


def test_beam_reuses_low_degree_neighbors_instead_of_decoding_twice(monkeypatch):
    raw = raw_bundle()
    bound = scope_bundle(raw, KnowledgeScope(kb_id="one"))
    populate(bound, KnowledgeScope(kb_id="one"))
    calls = []
    neighbors = bound.graph.neighbors

    def counted(name, **kwargs):
        calls.append(kwargs)
        return neighbors(name, **kwargs)

    monkeypatch.setattr(bound.graph, "neighbors", counted)
    beam = BeamExpander(bound.graph, BeamConfig())
    rows = beam.layer_edges(
        "Alice", preferred_relations=["WORKS_AT"], sub_question="works at", tenant_id="default"
    )
    assert rows and rows[0][1].type == "WORKS_AT"
    assert len(calls) == 1
    assert calls[0]["tenant_id"] == "default"


def test_shared_beam_prefixes_are_scored_once_without_losing_distinct_edges(monkeypatch):
    retriever = GraphRetriever(raw_bundle().graph)
    entity = EntityRecord("b", "Baker", "Company")
    edge = RelationRecord("r", "a", "b", "WORKS_AT", head_name="Alice", tail_name="Baker")
    newer = replace(edge, attributes={"confidence": 0.95})
    calls = []

    def score(relation, question):
        calls.append(relation)
        return relation.attributes.get("confidence", 0.5)

    monkeypatch.setattr(retriever, "_score_edge", score)
    beams = [
        BeamItem(1.0, "Baker", [], [], edges=[(edge, entity)]),
        BeamItem(1.0, "Other", [], [], edges=[(edge, entity), (newer, entity)]),
    ]
    ranked = retriever._rank_neighbor_edges(beams, "Alice", "employer")
    assert len(calls) == 2
    assert len(ranked) == 1 and ranked[0][1] is newer


def test_beam_join_reads_once_and_a_new_traversal_sees_mutations(monkeypatch):
    raw = raw_bundle()
    bound = scope_bundle(raw, KnowledgeScope(kb_id="one"))
    bound.graph.upsert_entities([EntityRecord(name, name, "Entity") for name in "ABCDE"])
    bound.graph.upsert_relations(
        [
            RelationRecord(a + b, a, b, "related", head_name=a, tail_name=b)
            for a, b in ["AB", "AC", "BD", "CD", "DE"]
        ]
    )
    calls = Counter()
    original = bound.graph.neighbors

    def counted(name, **kwargs):
        calls[name] += 1
        return original(name, **kwargs)

    monkeypatch.setattr(bound.graph, "neighbors", counted)
    beam = BeamExpander(bound.graph, BeamConfig())
    results = beam.beam_expand("A", max_hops=3, preferred_relations=None, sub_question=None)
    assert sum(item.node_name == "E" for item in results) == 2
    assert calls["D"] == 1
    bound.graph.delete_relation("DE")
    results = beam.beam_expand("A", max_hops=3, preferred_relations=None, sub_question=None)
    assert all(item.node_name != "E" for item in results)
    assert calls["D"] == 2
