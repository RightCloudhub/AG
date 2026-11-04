"""Semantic graph/source differences for a sealed generation publication."""

from __future__ import annotations

import json
from dataclasses import asdict

from agentic_graphrag.core.knowledge_versions import GraphChangeSet, WriteIntent
from agentic_graphrag.core.ports.stores import DocumentRecord, EntityRecord, RelationRecord
from agentic_graphrag.stores.snapshot_payload import SnapshotPayload


def generation_changes(
    intent: WriteIntent, before: SnapshotPayload, after: SnapshotPayload
) -> GraphChangeSet:
    old_entities = {row.id: row for row in before.entities}
    new_entities = {row.id: row for row in after.entities}
    old_relations = {row.id: row for row in before.relations}
    new_relations = {row.id: row for row in after.relations}
    old_docs = {row.doc_id: row for row in before.documents}
    new_docs = {row.doc_id: row for row in after.documents}
    docs_changed = changed_documents(old_docs, new_docs)
    return GraphChangeSet(
        scope=intent.scope,
        operation_id=intent.operation_id,
        source_generation=intent.source_generation,
        target_generation=intent.target_generation,
        entity_upserts=_upserts(old_entities, new_entities),
        entity_deletes=tuple(sorted(old_entities.keys() - new_entities.keys())),
        relation_upserts=_upserts(old_relations, new_relations),
        relation_deletes=tuple(sorted(old_relations.keys() - new_relations.keys())),
        document_ids=docs_changed,
        source_retractions=_source_retractions(before, after),
        topology_changed=_topology(
            old_entities,
            new_entities,
            before_relations=old_relations,
            after_relations=new_relations,
        ),
    )


def changed_documents(
    before: dict[str, DocumentRecord], after: dict[str, DocumentRecord]
) -> tuple[str, ...]:
    changed = set(before) ^ set(after)
    changed.update(
        doc_id
        for doc_id in before.keys() & after.keys()
        if (before[doc_id].version, before[doc_id].content_sha256)
        != (after[doc_id].version, after[doc_id].content_sha256)
    )
    return tuple(sorted(changed))


def _semantic(row: EntityRecord | RelationRecord) -> str:
    data = asdict(row)
    for key in ("tenant_id", "kb_id", "generation"):
        data.pop(key, None)
    return json.dumps(data, sort_keys=True, ensure_ascii=True)


def _upserts[T: (EntityRecord, RelationRecord)](
    before: dict[str, T], after: dict[str, T]
) -> tuple[str, ...]:
    return tuple(
        sorted(
            key
            for key, value in after.items()
            if key not in before or _semantic(before[key]) != _semantic(value)
        )
    )


def _topology(
    before_entities: dict[str, EntityRecord],
    after_entities: dict[str, EntityRecord],
    *,
    before_relations: dict[str, RelationRecord],
    after_relations: dict[str, RelationRecord],
) -> bool:
    if (
        before_entities.keys() != after_entities.keys()
        or before_relations.keys() != after_relations.keys()
    ):
        return True
    return any(
        (before_relations[key].head_id, before_relations[key].tail_id) != (row.head_id, row.tail_id)
        for key, row in after_relations.items()
    )


def _source_retractions(before: SnapshotPayload, after: SnapshotPayload) -> tuple[str, ...]:
    old = _sources(before)
    new = _sources(after)
    return tuple(sorted({doc_id for fingerprint, doc_id in old.items() if fingerprint not in new}))


def _sources(payload: SnapshotPayload) -> dict[str, str]:
    result = {}
    records: list[EntityRecord | RelationRecord] = [*payload.entities, *payload.relations]
    for record in records:
        for source in record.sources:
            doc_id = str(source.get("doc_id") or "")
            if doc_id:
                fingerprint = json.dumps(
                    [type(record).__name__, record.id, source], sort_keys=True, ensure_ascii=True
                )
                result[fingerprint] = doc_id
    return result
