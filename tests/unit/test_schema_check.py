from agentic_graphrag.config import resolve_path
from agentic_graphrag.knowledge.schema_check import (
    EntityMention,
    Triple,
    load_schema,
    validate_triple,
    validate_triples,
)


def test_load_schema_domain_v0():
    schema = load_schema(resolve_path("configs/schema/domain_v0.yaml"))
    assert "Company" in schema.entity_types
    assert "CEO_OF" in schema.relation_types


def test_valid_triple():
    schema = load_schema(resolve_path("configs/schema/domain_v0.yaml"))
    t = Triple(
        head=EntityMention(name="Elena Varga", type="Person"),
        relation="CEO_OF",
        tail=EntityMention(name="Apex Holdings", type="Company"),
        confidence=0.9,
    )
    assert validate_triple(t, schema) is None


def test_invalid_relation_type():
    schema = load_schema(resolve_path("configs/schema/domain_v0.yaml"))
    t = Triple(
        head=EntityMention(name="A", type="Company"),
        relation="NOT_A_REAL_REL",
        tail=EntityMention(name="B", type="Company"),
    )
    assert validate_triple(t, schema) is not None


def test_invalid_head_type_for_relation():
    schema = load_schema(resolve_path("configs/schema/domain_v0.yaml"))
    t = Triple(
        head=EntityMention(name="Widget", type="Product"),
        relation="CEO_OF",
        tail=EntityMention(name="Apex", type="Company"),
    )
    reason = validate_triple(t, schema)
    assert reason is not None
    assert "head type" in reason


def test_batch_validation():
    schema = load_schema(resolve_path("configs/schema/domain_v0.yaml"))
    good = Triple(
        head=EntityMention(name="A", type="Company"),
        relation="PARENT_OF",
        tail=EntityMention(name="B", type="Company"),
    )
    bad = Triple(
        head=EntityMention(name="A", type="Company"),
        relation="CEO_OF",
        tail=EntityMention(name="B", type="Company"),
    )
    result = validate_triples([good, bad], schema)
    assert len(result.accepted) == 1
    assert len(result.rejected) == 1
