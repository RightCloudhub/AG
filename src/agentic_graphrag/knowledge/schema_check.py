"""Schema loading and triple validation (FR-KG-03)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class EntityMention(BaseModel):
    name: str
    type: str


class Triple(BaseModel):
    head: EntityMention
    relation: str
    tail: EntityMention
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    source_span: str = ""
    source_doc_id: str = ""
    source_chunk_id: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


class ExtractResult(BaseModel):
    triples: list[Triple] = Field(default_factory=list)


@dataclass
class SchemaDefinition:
    version: str
    name: str
    entity_types: set[str]
    relation_types: dict[str, dict[str, Any]]
    raw: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [f"Schema: {self.name} v{self.version}", "Entity types:"]
        for et in sorted(self.entity_types):
            lines.append(f"  - {et}")
        lines.append("Relation types:")
        for rt, meta in sorted(self.relation_types.items()):
            lines.append(f"  - {rt}: {meta.get('head')} -> {meta.get('tail')} ({meta.get('description', '')})")
        return "\n".join(lines)


@dataclass
class ValidationResult:
    accepted: list[Triple]
    rejected: list[tuple[Triple, str]]


def load_schema(path: str | Path) -> SchemaDefinition:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    entity_types = set((data.get("entity_types") or {}).keys())
    relation_types = data.get("relation_types") or {}
    return SchemaDefinition(
        version=str(data.get("version", "0")),
        name=str(data.get("name", "unnamed")),
        entity_types=entity_types,
        relation_types=relation_types,
        raw=data,
    )


def _as_type_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, list):
        return {str(v) for v in value}
    return {str(value)}


def validate_triple(triple: Triple, schema: SchemaDefinition) -> str | None:
    """Return rejection reason or None if valid."""
    if triple.head.type not in schema.entity_types:
        return f"unknown head type: {triple.head.type}"
    if triple.tail.type not in schema.entity_types:
        return f"unknown tail type: {triple.tail.type}"
    if triple.relation not in schema.relation_types:
        return f"unknown relation: {triple.relation}"
    meta = schema.relation_types[triple.relation]
    heads = _as_type_set(meta.get("head"))
    tails = _as_type_set(meta.get("tail"))
    if heads and triple.head.type not in heads:
        return f"relation {triple.relation} head type {triple.head.type} not in {sorted(heads)}"
    if tails and triple.tail.type not in tails:
        return f"relation {triple.relation} tail type {triple.tail.type} not in {sorted(tails)}"
    if not triple.head.name.strip() or not triple.tail.name.strip():
        return "empty entity name"
    return None


def validate_triples(triples: list[Triple], schema: SchemaDefinition) -> ValidationResult:
    accepted: list[Triple] = []
    rejected: list[tuple[Triple, str]] = []
    for t in triples:
        reason = validate_triple(t, schema)
        if reason:
            rejected.append((t, reason))
        else:
            accepted.append(t)
    return ValidationResult(accepted=accepted, rejected=rejected)
