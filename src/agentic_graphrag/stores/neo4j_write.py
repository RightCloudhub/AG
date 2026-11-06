"""Schema identifiers and open logical types use distinct physical encodings."""

from __future__ import annotations

import re
from typing import Any

from agentic_graphrag.core.ports.stores import EntityRecord, RelationRecord
from agentic_graphrag.stores.document_provenance import merge_sources
from agentic_graphrag.stores.neo4j_codec import (
    attrs_for_neo4j,
    sources_for_neo4j,
    sources_from_neo4j,
)

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
OPEN_ENTITY_LABEL = "AG_ENTITY"
OPEN_RELATION_TYPE = "AG_RELATION"


def validate_identifier(value: str, kind: str) -> str:
    if not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {kind} identifier: {value!r}")
    return value


def write_entity(session: Any, ent: EntityRecord) -> bool:
    label = (
        OPEN_ENTITY_LABEL
        if ent.attributes.get("graph_mode") == "open"
        else validate_identifier(ent.type, "label")
    )
    query = (
        f"MERGE (e:`{label}` {{id: $id, tenant_id: $tenant_id}}) "
        "SET e.name=$name, e.entity_type=$entity_type, e.kb_id=$kb_id, e.generation=$generation, "
        "e.attributes=$attributes, e.aliases=$aliases, e.sources=$sources RETURN e.id AS id"
    )
    return (
        session.run(
            query,
            id=ent.id,
            name=ent.name,
            entity_type=ent.type,
            tenant_id=ent.tenant_id,
            kb_id=ent.kb_id,
            generation=ent.generation,
            attributes=attrs_for_neo4j(ent.attributes),
            aliases=list(ent.aliases),
            sources=sources_for_neo4j(ent.sources),
        ).single()
        is not None
    )


def write_relation(session: Any, rel: RelationRecord) -> bool:
    old = session.run(
        "MATCH ()-[r]->() WHERE r.id=$id AND r.tenant_id=$tenant_id RETURN r.sources AS sources",
        id=rel.id,
        tenant_id=rel.tenant_id,
    ).single()
    sources = merge_sources(sources_from_neo4j(old["sources"]) if old else [], rel.sources)
    physical = (
        OPEN_RELATION_TYPE
        if rel.attributes.get("graph_mode") == "open"
        else validate_identifier(rel.type, "rel")
    )
    query = (
        "MERGE (h {id: $head_id, tenant_id: $tenant_id}) ON CREATE SET h.name=$head_name "
        "MERGE (t {id: $tail_id, tenant_id: $tenant_id}) ON CREATE SET t.name=$tail_name "
        f"MERGE (h)-[r:`{physical}` {{id: $id, tenant_id: $tenant_id}}]->(t) "
        "SET r.confidence=$confidence, r.attributes=$attributes, r.predicate=$predicate, "
        "r.kb_id=$kb_id, r.generation=$generation, r.head_id=$head_id, r.tail_id=$tail_id, "
        "r.head_name=$head_name, r.tail_name=$tail_name, r.sources=$sources RETURN r.id AS id"
    )
    return (
        session.run(
            query,
            id=rel.id,
            head_id=rel.head_id,
            tail_id=rel.tail_id,
            head_name=rel.head_name,
            tail_name=rel.tail_name,
            confidence=rel.confidence,
            tenant_id=rel.tenant_id,
            kb_id=rel.kb_id,
            generation=rel.generation,
            predicate=rel.type,
            attributes=attrs_for_neo4j(rel.attributes),
            sources=sources_for_neo4j(sources),
        ).single()
        is not None
    )
