"""Load immutable KB-specific schemas without mutating global configuration."""

import re
from typing import Any

from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.core.schema_drafts import SchemaRegistry
from agentic_graphrag.knowledge.schema_check import SchemaDefinition

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def schema_from_mapping(raw: dict[str, Any]) -> SchemaDefinition:
    entity_types, relations = raw.get("entity_types"), raw.get("relation_types")
    if not isinstance(entity_types, dict) or not isinstance(relations, dict):
        raise ValueError("Schema must declare entity_types and relation_types mappings")
    if any(
        not isinstance(name, str) or not _IDENTIFIER.fullmatch(name)
        for name in [*entity_types, *relations]
    ):
        raise ValueError("Schema types must be safe physical identifiers")
    for relation in relations.values():
        _check_relation(relation, entity_types)
    return SchemaDefinition(
        name=str(raw.get("name", "unnamed")),
        version=str(raw.get("version", "1")),
        entity_types=set(entity_types),
        relation_types=dict(relations),
        raw=raw,
    )


def _check_relation(relation: Any, entity_types: dict[str, Any]) -> None:
    if not isinstance(relation, dict):
        raise ValueError("Invalid Schema relation definition")
    for endpoint in (relation.get("head", []), relation.get("tail", [])):
        types = endpoint if isinstance(endpoint, list) else [endpoint]
        if any(not isinstance(t, str) or t not in entity_types for t in types):
            raise ValueError("Schema relation endpoint is not a declared entity type")


def schema_for_knowledge_base(kb: KnowledgeBase, registry: SchemaRegistry) -> SchemaDefinition:
    if kb.build_mode != "schema" or not kb.schema_id or not kb.schema_version:
        raise ValueError("Knowledge base does not bind a versioned Schema")
    raw = registry.get_schema(kb.scope, schema_id=kb.schema_id, version=kb.schema_version)
    if raw is None:
        raise ValueError("The configured Schema version is not available")
    return schema_from_mapping(raw)
