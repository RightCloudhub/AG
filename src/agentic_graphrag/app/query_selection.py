"""Build an immutable, authorized document projection before retrieval or summaries."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from agentic_graphrag.app.errors import INVALID_INPUT, ApiError
from agentic_graphrag.core.knowledge_versions import KnowledgeSnapshot
from agentic_graphrag.core.ports.stores import EntityRecord, RelationRecord
from agentic_graphrag.stores.knowledge_store import InMemoryKnowledgeStore
from agentic_graphrag.stores.snapshot_bundle import capture_snapshot, snapshot_bundle
from agentic_graphrag.stores.snapshot_payload import (
    SnapshotPayload,
    document_fingerprint,
    visible_payload,
)

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def select_documents(
    svc: QueryService, doc_ids: list[str] | None, *, tenant_id: str
) -> QueryService:
    kb = svc.knowledge_base
    if kb is None:
        return svc
    saved = (
        svc.knowledge_store.get_snapshot(kb.scope, kb.active_generation)
        if svc.knowledge_store
        else None
    )
    if saved is None and doc_ids is None and kb.kb_id == "default":
        return svc
    manifest, serialized = saved or _capture(svc)
    payload = SnapshotPayload.model_validate_json(serialized)
    if svc.knowledge_store is not None:
        payload = visible_payload(payload, svc.knowledge_store)
    if doc_ids is not None:
        payload = _project(payload, set(doc_ids))
    manifest = manifest.model_copy(
        update={
            "payload_sha256": "",
            "document_selection_fingerprint": document_fingerprint(payload.documents),
        }
    )
    temporary = InMemoryKnowledgeStore()
    try:
        manifest = temporary.save_snapshot(manifest, payload.model_dump_json())
        return replace(
            svc,
            bundle=snapshot_bundle(temporary, kb.scope, kb.active_generation),
            snapshot=manifest,
            enable_cache=False,
        )
    finally:
        temporary.close()


def _capture(svc: QueryService) -> tuple[KnowledgeSnapshot, str]:
    kb = svc.knowledge_base
    assert kb is not None
    manifest = KnowledgeSnapshot(
        scope=kb.scope,
        generation=kb.active_generation,
        graph_version=kb.active_generation,
        vector_version=kb.active_generation,
        schema_version=kb.schema_version or "",
        model_config_revision=svc.cfg.models.profile_revision,
    )
    temporary = InMemoryKnowledgeStore()
    try:
        capture_snapshot(svc.bundle, manifest, store=temporary)
        saved = temporary.get_snapshot(kb.scope, kb.active_generation)
        assert saved is not None
        return saved
    finally:
        temporary.close()


def _sources(sources: list[dict[str, Any]], selected: set[str]) -> list[dict[str, Any]]:
    return [source for source in sources if source.get("doc_id") in selected]


def _scoped_record[T: (EntityRecord, RelationRecord)](record: T, selected: set[str]) -> T:
    sources = _sources(record.sources, selected)
    attributes = dict(record.attributes)
    if len(sources) != len(record.sources):
        # A merged description can contain facts outside the selected documents.
        attributes.pop("description", None)
    result = replace(record, sources=sources, attributes=attributes)
    if isinstance(result, EntityRecord) and len(sources) != len(record.sources):
        result.aliases = []
    return result


def _project(payload: SnapshotPayload, selected: set[str]) -> SnapshotPayload:
    documents = [doc for doc in payload.documents if doc.doc_id in selected]
    if {doc.doc_id for doc in documents} != selected:
        raise ApiError(
            INVALID_INPUT, "One or more selected documents are unavailable", status_code=404
        )
    relations = [
        _scoped_record(rel, selected)
        for rel in payload.relations
        if _sources(rel.sources, selected)
    ]
    entities = _project_entities(payload.entities, relations, selected=selected)
    return payload.model_copy(
        update={"documents": documents, "entities": entities, "relations": relations}
    )


def _project_entities(
    entities: list[EntityRecord], relations: list[RelationRecord], *, selected: set[str]
) -> list[EntityRecord]:
    endpoints = {identifier for rel in relations for identifier in (rel.head_id, rel.tail_id)}
    return [
        _scoped_record(ent, selected)
        for ent in entities
        if ent.id in endpoints or _sources(ent.sources, selected)
    ]
