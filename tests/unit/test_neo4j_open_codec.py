"""Free predicates stay parameters while physical Neo4j identifiers remain fixed."""

import json
from unittest.mock import MagicMock

import pytest

from agentic_graphrag.core.ports.stores import EntityRecord, RelationRecord
from agentic_graphrag.stores.neo4j_codec import node_to_entity, rel_to_record
from agentic_graphrag.stores.neo4j_queries import neighbors_query
from agentic_graphrag.stores.neo4j_write import write_entity, write_relation


class Node(dict):
    labels = {"AG_ENTITY"}


class Edge(dict):
    type = "AG_RELATION"


def test_open_entity_and_relation_encode_free_text_without_interpolating_it():
    session = MagicMock()
    session.run.return_value.single.return_value = {"sources": "[]", "id": "r"}
    name = "关系` MATCH (n) DETACH DELETE n //"
    entity = EntityRecord(
        id="a",
        name="王伟",
        type="自然人（作者）",
        attributes={"graph_mode": "open"},
        aliases=["老王"],
        tenant_id="t",
        sources=[{"doc_id": "d"}],
    )
    assert write_entity(session, entity)
    query, kwargs = session.run.call_args[0][0], session.run.call_args.kwargs
    assert "AG_ENTITY" in query and "自然人" not in query
    decoded = node_to_entity(
        Node(
            id="a",
            name="王伟",
            entity_type=kwargs["entity_type"],
            aliases=kwargs["aliases"],
            sources=kwargs["sources"],
        )
    )
    assert decoded.type == entity.type and decoded.aliases == ["老王"]
    assert decoded.sources == [{"doc_id": "d"}]
    relation = RelationRecord(
        id="r",
        type=name,
        head_id="a",
        tail_id="b",
        tenant_id="t",
        attributes={"graph_mode": "open"},
    )
    assert write_relation(session, relation)
    query, kwargs = session.run.call_args[0][0], session.run.call_args.kwargs
    assert "AG_RELATION" in query and name not in query and kwargs["predicate"] == name
    assert rel_to_record(Edge(id="r", predicate=name)).type == name
    assert json.loads(kwargs["attributes"])["graph_mode"] == "open"


def test_schema_identifiers_still_validate_and_logical_relation_filters_are_parameterized():
    with pytest.raises(ValueError, match="Invalid label"):
        write_entity(MagicMock(), EntityRecord(id="e", name="N", type="bad` label"))
    assert "r.predicate" in neighbors_query(1, ["负责"])
    assert ".predicate" in neighbors_query(2, ["负责"])
    assert "负责" not in neighbors_query(1, ["负责"])
