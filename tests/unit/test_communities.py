"""Source-deduplicated projection and deterministic optional community detection."""

from dataclasses import replace

import pytest

from agentic_graphrag.core.knowledge import KnowledgeScope
from agentic_graphrag.core.knowledge_versions import GraphChangeSet
from agentic_graphrag.core.ports.stores import EntityRecord, RelationRecord
from agentic_graphrag.knowledge.communities import ConnectedComponentsDetector, LeidenDetector
from agentic_graphrag.knowledge.community_incremental import update_communities
from agentic_graphrag.knowledge.community_projection import project_graph
from agentic_graphrag.stores.community_index import CommunityIndexStore


def entities(names="abcdef"):
    return [EntityRecord(id=name, name=name, type="Entity") for name in names]


def edge(left, right, *, doc=None, identifier=None):
    return RelationRecord(
        id=identifier or f"{left}-{right}",
        head_id=left,
        tail_id=right,
        head_name=left,
        tail_name=right,
        type="related",
        sources=[{"doc_id": doc or f"doc-{left}-{right}", "doc_version": 1}],
    )


def project(records, *, generation="1", names="abcdef", hashes=None):
    return project_graph(
        KnowledgeScope(),
        generation,
        entities=entities(names),
        relations=records,
        document_hashes=hashes,
    )


def change(source="1", target="2"):
    return GraphChangeSet(
        scope=KnowledgeScope(),
        operation_id="update",
        source_generation=source,
        target_generation=target,
        topology_changed=False,
    )


class CountingDetector(ConnectedComponentsDetector):
    def __init__(self):
        self.inputs = []

    def detect(self, projection, *, seed=0):
        self.inputs.append(tuple(node.id for node in projection.nodes))
        return super().detect(projection, seed=seed)


def test_projection_deduplicates_overlap_reverse_relations_and_duplicate_documents():
    first = edge("a", "b", doc="one")
    overlap = replace(first, id="overlap", sources=[{"doc_id": "one", "chunk_id": "other"}])
    reverse = edge("b", "a", doc="duplicate")
    projection = project(
        [first, overlap, reverse],
        names="abc",
        hashes={"one": "same-content", "duplicate": "same-content"},
    )
    assert len(projection.edges) == 1
    assert projection.edges[0].weight == 1
    assert projection.edges[0].document_ids == ("duplicate", "one")
    assert len(projection.nodes) == 3
    distinct = project([first, edge("b", "a", doc="two")], names="abc")
    assert distinct.edges[0].weight == 2
    communities = ConnectedComponentsDetector().detect(projection)
    assert {group.entity_ids for group in communities} == {("a", "b"), ("c",)}


def test_projection_rejects_foreign_scope_and_missing_endpoints():
    with pytest.raises(ValueError, match="scope"):
        project_graph(
            KnowledgeScope(),
            "1",
            entities=[EntityRecord(id="a", name="a", type="T", tenant_id="other")],
            relations=[],
        )
    with pytest.raises(ValueError, match="endpoint"):
        project([edge("a", "missing")], names="ab")
    duplicate = entities("aa")
    with pytest.raises(ValueError, match="duplicate"):
        project_graph(KnowledgeScope(), "1", entities=duplicate, relations=[])


def test_graph_content_update_refreshes_only_dependent_community_without_repartition():
    records = [edge("a", "b"), edge("c", "d"), edge("e", "f")]
    before = project(records)
    after = project(
        [replace(records[0], attributes={"description": "changed risk"}), *records[1:]],
        generation="2",
    )
    detector = CountingDetector()
    previous = ConnectedComponentsDetector().detect(before)
    result = update_communities(
        before, after, previous=previous, change=change(), detector=detector
    )
    assert not detector.inputs
    assert not result.topology_changed
    assert len(result.changed_community_ids) == 1
    assert len(result.reused_community_ids) == 2
    assert all(group.generation == "2" for group in result.communities)
    old_hashes = {group.id: group.input_fingerprint for group in previous}
    assert all(
        group.input_fingerprint == old_hashes[group.id]
        for group in result.communities
        if group.id in result.reused_community_ids
    )


def test_cross_component_edge_and_bridge_deletion_expand_old_and_new_components():
    records = [edge("a", "b"), edge("c", "d"), edge("e", "f")]
    before = project(records)
    joined = project([*records, edge("b", "c")], generation="2")
    detector = CountingDetector()
    initial = ConnectedComponentsDetector().detect(before)
    added = update_communities(before, joined, previous=initial, change=change(), detector=detector)
    assert added.recomputed_entity_ids == ("a", "b", "c", "d")
    assert detector.inputs == [("a", "b", "c", "d")]
    assert len(added.reused_community_ids) == 1
    assert not added.full_rebuild and not added.exact_full_partition
    split = project(records, generation="3")
    removed = update_communities(
        joined, split, previous=added.communities, change=change("2", "3"), detector=detector
    )
    assert removed.recomputed_entity_ids == ("a", "b", "c", "d")
    assert {item.entity_ids for item in removed.communities} == {("a", "b"), ("c", "d"), ("e", "f")}


def test_weight_changes_override_false_topology_hint_and_isolated_node_is_covered():
    records = [edge("a", "b"), edge("c", "d"), edge("e", "f")]
    before = project(records)
    weighted = project([*records, edge("a", "b", doc="extra", identifier="second")], generation="2")
    old = ConnectedComponentsDetector().detect(before)
    update = update_communities(
        before, weighted, previous=old, change=change(), detector=ConnectedComponentsDetector()
    )
    assert update.topology_changed
    assert update.recomputed_entity_ids == ("a", "b")
    isolated = project(records, generation="2", names="abcdefg")
    update = update_communities(
        before, isolated, previous=old, change=change(), detector=ConnectedComponentsDetector()
    )
    assert ("g",) in {group.entity_ids for group in update.communities}
    assert update.recomputed_entity_ids == ("g",)


def test_document_content_hash_and_self_loop_change_report_fingerprints():
    records = [edge("a", "a", doc="doc")]
    before = project(records, names="a", hashes={"doc": "one"})
    after = project(records, names="a", hashes={"doc": "two"}, generation="2")
    first = ConnectedComponentsDetector().detect(before)[0]
    second = ConnectedComponentsDetector().detect(after)[0]
    assert first.topology_fingerprint == second.topology_fingerprint
    assert first.input_fingerprint != second.input_fingerprint
    assert first.document_ids == ("doc",)


def test_missing_optional_dependency_is_explicit_and_empty_graph_never_imports(monkeypatch):
    from agentic_graphrag.knowledge import communities

    def missing(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(communities.importlib, "import_module", missing)
    assert LeidenDetector().detect(project([], names="")) == ()
    with pytest.raises(RuntimeError, match="optional summary"):
        LeidenDetector().detect(project([], names="a"))
    with pytest.raises(ValueError):
        LeidenDetector(resolution=float("nan"))


def test_real_leiden_is_reproducible_from_reordered_records():
    pytest.importorskip("leidenalg")
    names = "abcdefg"
    records = [
        edge("a", "b"),
        edge("b", "c"),
        edge("a", "c"),
        edge("d", "e"),
        edge("e", "f"),
        edge("d", "f"),
    ]
    first = project_graph(KnowledgeScope(), "1", entities=entities(names), relations=records)
    second = project_graph(
        KnowledgeScope(), "1", entities=entities(names[::-1]), relations=list(reversed(records))
    )
    detector = LeidenDetector()
    result = detector.detect(first, seed=42)
    assert result == detector.detect(second, seed=42)
    assert {key for item in result for key in item.entity_ids} == set(names)
    assert ("g",) in {item.entity_ids for item in result}


def test_large_repair_uses_explicit_full_baseline_and_invalid_partition_is_rejected():
    before = project([edge("a", "b")], names="ab")
    after = project([], names="ab", generation="2")
    detector = ConnectedComponentsDetector()
    old = detector.detect(before)
    updated = update_communities(before, after, previous=old, change=change(), detector=detector)
    assert updated.full_rebuild and updated.exact_full_partition
    with pytest.raises(ValueError, match="cover every"):
        update_communities(before, after, previous=(), change=change(), detector=detector)


def test_community_partition_survives_reopen_and_cannot_be_replaced(tmp_path):
    path = tmp_path / "metadata.db"
    projection = project([edge("a", "b"), edge("c", "d")], names="abcd")
    partition = ConnectedComponentsDetector().detect(projection)
    store = CommunityIndexStore(path)
    store.save(projection, partition)
    store.save(projection, partition)
    assert store.get(KnowledgeScope(kb_id="other"), "1") is None
    assert store.get(KnowledgeScope(), "missing") is None
    store.close()
    reopened = CommunityIndexStore(path)
    assert reopened.get(KnowledgeScope(), "1") == (projection, partition)
    altered = project([edge("a", "b")], names="abcd")
    with pytest.raises(ValueError, match="overwritten"):
        reopened.save(altered, ConnectedComponentsDetector().detect(altered))
    with pytest.raises(ValueError, match="exactly one"):
        reopened.save(projection, ())
    reopened.close()
