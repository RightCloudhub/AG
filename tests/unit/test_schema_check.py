from agentic_graphrag.config import resolve_path
from agentic_graphrag.knowledge.schema_check import (
    EntityMention,
    Triple,
    filter_by_confidence,
    gate_triples,
    load_schema,
    validate_triple,
    validate_triples,
)


def _triple(
    head="Elena Varga",
    htype="Person",
    rel="CEO_OF",
    tail="Apex Holdings",
    ttype="Company",
    conf=0.9,
) -> Triple:
    return Triple(
        head=EntityMention(name=head, type=htype),
        relation=rel,
        tail=EntityMention(name=tail, type=ttype),
        confidence=conf,
    )


def test_load_schema_domain_v0():
    schema = load_schema(resolve_path("configs/schema/domain_v0.yaml"))
    assert "Company" in schema.entity_types
    assert "CEO_OF" in schema.relation_types


def test_valid_triple():
    schema = load_schema(resolve_path("configs/schema/domain_v0.yaml"))
    assert validate_triple(_triple(), schema) is None


def test_invalid_relation_type():
    schema = load_schema(resolve_path("configs/schema/domain_v0.yaml"))
    t = _triple(rel="NOT_A_REAL_REL")
    assert validate_triple(t, schema) is not None


def test_invalid_head_type_for_relation():
    schema = load_schema(resolve_path("configs/schema/domain_v0.yaml"))
    t = _triple(head="Widget", htype="Product")
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


def test_confidence_filter():
    high = _triple(conf=0.9)
    low = _triple(conf=0.2, head="Marcus Chen", tail="NovaTech Industries")
    result = filter_by_confidence([high, low], 0.5)
    assert len(result.accepted) == 1
    assert len(result.rejected) == 1
    assert "confidence" in result.rejected[0][1]


def test_gate_triples_schema_and_confidence():
    schema = load_schema(resolve_path("configs/schema/domain_v0.yaml"))
    ok = _triple(conf=0.9)
    low_conf = _triple(conf=0.1)
    bad_schema = _triple(rel="NOT_REAL", conf=0.99)
    gated = gate_triples([ok, low_conf, bad_schema], schema, confidence_threshold=0.5)
    assert len(gated.accepted) == 1
    assert len(gated.rejected) == 2
    assert gated.rejection_reasons  # non-empty buckets
    records = gated.to_reject_records()
    assert all("reason" in r and "triple" in r for r in records)
