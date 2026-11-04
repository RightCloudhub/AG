"""Serialization and visibility of immutable generation records."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, model_validator

from agentic_graphrag.core.knowledge import KnowledgeScope
from agentic_graphrag.core.ports.knowledge import KnowledgeStore
from agentic_graphrag.core.ports.stores import (
    ChunkRecord,
    DocumentRecord,
    EntityRecord,
    RelationRecord,
)


class SnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: KnowledgeScope
    generation: str
    documents: list[DocumentRecord]
    entities: list[EntityRecord]
    relations: list[RelationRecord]

    @model_validator(mode="after")
    def validate_ownership(self) -> Self:
        records: list[DocumentRecord | EntityRecord | RelationRecord | ChunkRecord] = [
            *self.documents,
            *self.entities,
            *self.relations,
        ]
        records.extend(chunk for doc in self.documents for chunk in doc.chunks)
        for record in records:
            if (record.tenant_id or "default", record.kb_id, record.generation) != (
                self.scope.tenant_id,
                self.scope.kb_id,
                self.generation,
            ):
                raise ValueError("Snapshot record belongs to another scope or generation")
        return self


def document_fingerprint(documents: list[DocumentRecord]) -> str:
    pairs = sorted((doc.doc_id, doc.version, doc.content_sha256) for doc in documents)
    return hashlib.sha256(repr(pairs).encode()).hexdigest()


def visible_payload(payload: SnapshotPayload, store: KnowledgeStore) -> SnapshotPayload:
    versions = {doc.doc_id: doc.version for doc in payload.documents}
    documents = [
        doc
        for doc in payload.documents
        if not store.is_tombstoned(payload.scope, doc.doc_id, version=doc.version)
    ]
    entities = _visible_entities(payload, store, versions=versions)
    relations = _visible_relations(payload, store, versions=versions, entities=entities)
    return payload.model_copy(
        update={
            "documents": documents,
            "entities": entities,
            "relations": relations,
        }
    )


def _visible_entities(
    payload: SnapshotPayload, store: KnowledgeStore, *, versions: dict[str, int]
) -> list[EntityRecord]:
    result = []
    for entity in payload.entities:
        sources = visible_sources(entity.sources, payload.scope, store=store, versions=versions)
        if not entity.sources or sources:
            result.append(replace(entity, sources=sources))
    return result


def _visible_relations(
    payload: SnapshotPayload,
    store: KnowledgeStore,
    *,
    versions: dict[str, int],
    entities: list[EntityRecord],
) -> list[RelationRecord]:
    entity_ids = {entity.id for entity in entities}
    result = []
    for relation in payload.relations:
        sources = visible_sources(relation.sources, payload.scope, store=store, versions=versions)
        if relation.sources and not sources:
            continue
        if {relation.head_id, relation.tail_id} <= entity_ids:
            result.append(replace(relation, sources=sources))
    return result


def visible_sources(
    sources: list[dict[str, Any]],
    scope: KnowledgeScope,
    *,
    store: KnowledgeStore,
    versions: dict[str, int],
) -> list[dict[str, Any]]:
    result = []
    for source in sources:
        doc_id = str(source.get("doc_id") or "")
        version = int(source.get("doc_version") or source.get("version") or versions.get(doc_id, 1))
        if doc_id and store.is_tombstoned(scope, doc_id, version=version):
            continue
        result.append(source)
    return result
