"""Restore seeded shadow indexes after process-local backends have restarted."""

from typing import Any

from agentic_graphrag.core.ports.capabilities import ProcessLocalIndex
from agentic_graphrag.core.ports.stores import DocumentRecord, EntityRecord, RelationRecord
from agentic_graphrag.core.source_projection import with_sources
from agentic_graphrag.knowledge.generation_changes import changed_documents
from agentic_graphrag.stores.factory import StoreBundle
from agentic_graphrag.stores.snapshot_payload import SnapshotPayload


def index_generations(bundle: StoreBundle) -> tuple[tuple[str, str], ...]:
    indexes = {
        "embedded": bundle.vector,
        "fulltext_indexed": bundle.fulltext,
        "graph_extracted": bundle.graph,
    }
    return tuple(
        (stage, index.index_generation)
        for stage, index in indexes.items()
        if isinstance(index, ProcessLocalIndex)
    )


def restore_seeded(
    bundle: StoreBundle, source: SnapshotPayload, *, previous: tuple[tuple[str, str], ...]
) -> None:
    current = dict(index_generations(bundle))
    previous_map = dict(previous)
    stale = {
        stage for stage, generation in current.items() if previous_map.get(stage) != generation
    }
    if not stale:
        return
    tenant = source.scope.tenant_id
    documents = {
        doc.doc_id: doc
        for doc_id in bundle.docs.list_ids(tenant_id=tenant)
        if (doc := bundle.docs.get(doc_id, tenant_id=tenant)) is not None
    }
    changed = set(changed_documents({doc.doc_id: doc for doc in source.documents}, documents))
    if "graph_extracted" in stale:
        _restore_graph(bundle, source, excluded=changed)
    for doc in documents.values():
        _restore_document(
            bundle, doc, stale=stale, changed=doc.doc_id in changed, generations=current
        )


def _retained_sources(sources: list[dict[str, Any]], excluded: set[str]) -> list[dict[str, Any]]:
    return [source for source in sources if str(source.get("doc_id") or "") not in excluded]


def _retained[T: (EntityRecord, RelationRecord)](records: list[T], excluded: set[str]) -> list[T]:
    result = []
    for record in records:
        sources = _retained_sources(record.sources, excluded)
        if sources or not record.sources:
            result.append(with_sources(record, sources))
    return result


def _restore_graph(bundle: StoreBundle, source: SnapshotPayload, *, excluded: set[str]) -> None:
    entities = _retained(source.entities, excluded)
    ids = {entity.id for entity in entities}
    relations = [
        relation
        for relation in _retained(source.relations, excluded)
        if {relation.head_id, relation.tail_id} <= ids
    ]
    bundle.graph.upsert_entities(entities)
    bundle.graph.upsert_relations(relations)


def _restore_document(
    bundle: StoreBundle,
    doc: DocumentRecord,
    *,
    stale: set[str],
    changed: bool,
    generations: dict[str, str],
) -> None:
    _restore_chunk_indexes(bundle, doc, stale=stale)
    if "graph_extracted" in stale and changed:
        doc.metadata.setdefault("graph_recovery_index_complete", doc.metadata.get("index_complete"))
        for chunk in doc.chunks:
            chunk.metadata.setdefault("graph_recovery_pending", chunk.graph_extracted)
            chunk.graph_extracted = False
        doc.metadata["index_complete"] = False
    doc.metadata["index_generations"] = generations
    bundle.docs.save(doc)


def _restore_chunk_indexes(bundle: StoreBundle, doc: DocumentRecord, *, stale: set[str]) -> None:
    if "embedded" in stale:
        bundle.vector.upsert([chunk for chunk in doc.chunks if chunk.embedding and chunk.embedded])
    if "fulltext_indexed" in stale:
        bundle.fulltext.index([chunk for chunk in doc.chunks if chunk.fulltext_indexed])
