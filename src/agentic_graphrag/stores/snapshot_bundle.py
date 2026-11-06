"""Capture and read actual immutable data generations, with deletion precedence."""

from __future__ import annotations

from dataclasses import replace

from agentic_graphrag.core.knowledge import KnowledgeScope
from agentic_graphrag.core.knowledge_versions import KnowledgeSnapshot
from agentic_graphrag.core.ports.capabilities import ProcessLocalIndex
from agentic_graphrag.core.ports.knowledge import KnowledgeStore
from agentic_graphrag.stores.doc_store import InMemoryDocStore
from agentic_graphrag.stores.factory import GraphBackend, StoreBundle, VectorBackend
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.scoped_bundle import scope_bundle
from agentic_graphrag.stores.scoped_graph import ScopedGraphStore
from agentic_graphrag.stores.scoped_graph_scan import entities_in_scope, relations_in_scope
from agentic_graphrag.stores.snapshot_payload import (
    SnapshotPayload,
    document_fingerprint,
    visible_payload,
)
from agentic_graphrag.stores.vector_store import InMemoryVectorStore


def capture_snapshot(
    bundle: StoreBundle, manifest: KnowledgeSnapshot, *, store: KnowledgeStore
) -> KnowledgeSnapshot:
    """Caller must serialize publication with the write lifecycle's lock.

    The serialized payload contains full source versions and index inputs, so
    published reads never consult mutable live indexes for the captured generation.
    """
    if not isinstance(bundle.graph, ScopedGraphStore):
        raise ValueError("Snapshots require a knowledge-bound bundle")
    binding = bundle.graph.binding
    if (binding.scope, binding.generation) != (manifest.scope, manifest.generation):
        raise ValueError("Snapshot manifest does not describe the bound storage generation")
    tenant = manifest.scope.tenant_id
    with bundle.docs.lock():
        documents = [
            doc
            for doc_id in bundle.docs.list_ids(tenant_id=tenant)
            if (doc := bundle.docs.get(doc_id, tenant_id=tenant)) is not None
        ]
        payload = SnapshotPayload(
            scope=manifest.scope,
            generation=manifest.generation,
            documents=documents,
            entities=list(entities_in_scope(bundle.graph, tenant)),
            relations=list(relations_in_scope(bundle.graph, tenant)),
        )
        payload = visible_payload(payload, store)
        manifest = manifest.model_copy(
            update={
                "document_selection_fingerprint": document_fingerprint(payload.documents),
            }
        )
        return store.save_snapshot(manifest, payload.model_dump_json())


def snapshot_bundle(store: KnowledgeStore, scope: KnowledgeScope, generation: str) -> StoreBundle:
    """Rehydrate immutable records; tombstones always override historical visibility.

    Re-resolve visibility before returning an in-flight answer if deletion can
    occur concurrently. This adapter is an immutable point-in-time read view.
    """
    snapshot = store.get_snapshot(scope, generation)
    if snapshot is None:
        raise ValueError("Knowledge snapshot does not exist")
    _, serialized = snapshot
    payload = SnapshotPayload.model_validate_json(serialized)
    if (payload.scope, payload.generation) != (scope, generation):
        raise ValueError("Snapshot payload ownership does not match the manifest")
    payload = visible_payload(payload, store)
    raw = StoreBundle(
        graph=InMemoryGraphStore(),
        vector=InMemoryVectorStore(),
        fulltext=BM25FulltextStore(),
        docs=InMemoryDocStore(),
        graph_backend=GraphBackend.MEMORY,
        vector_backend=VectorBackend.MEMORY,
    )
    bound = scope_bundle(raw, scope, generation=generation)
    restore_records(bound, payload)
    return scope_bundle(raw, scope, generation=generation, read_only=True)


def restore_records(bundle: StoreBundle, payload: SnapshotPayload) -> None:
    """Idempotently materialize a sealed source before beginning document changes."""
    bundle.graph.upsert_entities(payload.entities)
    bundle.graph.upsert_relations(payload.relations)
    for doc in payload.documents:
        bundle.fulltext.index([replace(chunk) for chunk in doc.chunks if chunk.fulltext_indexed])
        embedded = [replace(chunk) for chunk in doc.chunks if chunk.embedding and chunk.embedded]
        if embedded:
            bundle.vector.upsert(embedded)
        indexes = {
            "embedded": bundle.vector,
            "fulltext_indexed": bundle.fulltext,
            "graph_extracted": bundle.graph,
        }
        doc.metadata["index_generations"] = {
            stage: index.index_generation
            for stage, index in indexes.items()
            if isinstance(index, ProcessLocalIndex)
        }
        bundle.docs.save(doc)
