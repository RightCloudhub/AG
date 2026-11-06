"""Document lifecycle wiring and read-cache invalidation."""

import hashlib
from dataclasses import replace

from agentic_graphrag.app.entity_generations import carry_entities
from agentic_graphrag.app.knowledge_scope import knowledge_store
from agentic_graphrag.app.knowledge_writes import _legacy_write
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.knowledge.document_lifecycle import DocumentLifecycle
from agentic_graphrag.knowledge.generation_pipeline import GenerationPipeline


class TrackedLifecycle(DocumentLifecycle):
    def __init__(self, svc: QueryService) -> None:
        super().__init__(
            svc.bundle.docs,
            vector=svc.bundle.vector,
            fulltext=svc.bundle.fulltext,
            graph=svc.bundle.graph,
        )
        self.service = svc

    def delete(self, doc_id: str, *, tenant_id: str) -> None:
        doc = self.docs.get(doc_id, tenant_id=tenant_id)
        kb = self.service.knowledge_base
        if doc is not None and kb is not None and not _legacy_write(self.service):
            self._delete_generation(doc_id, tenant_id=tenant_id, version=doc.version)
            return
        if doc is not None and kb is not None:
            knowledge_store(self.service).tombstone(kb.scope, doc_id, through_version=doc.version)
        super().delete(doc_id, tenant_id=tenant_id)

    def _delete_generation(self, doc_id: str, *, tenant_id: str, version: int) -> None:
        kb = self.service.knowledge_base
        assert kb is not None
        root = self.service.parent or self.service
        metadata = knowledge_store(root)
        operation = "delete-" + hashlib.sha256(f"{doc_id}:{version}".encode()).hexdigest()
        pipeline = GenerationPipeline(root.bundle, metadata)
        write = pipeline.prepare(kb.scope, operation_id=operation, doc_ids=(doc_id,))
        metadata.tombstone(kb.scope, doc_id, through_version=version)
        lifecycle = DocumentLifecycle(
            write.bundle.docs,
            vector=write.bundle.vector,
            fulltext=write.bundle.fulltext,
            graph=write.bundle.graph,
        )
        if write.bundle.docs.get(doc_id, tenant_id=tenant_id) is not None:
            lifecycle.delete(doc_id, tenant_id=tenant_id)
        carry_entities(
            replace(
                self.service,
                bundle=write.bundle,
                knowledge_base=kb.model_copy(update={"active_generation": write.target_generation}),
            )
        )
        pipeline.complete(write)
        index_changed(root)


def lifecycle_for(svc: QueryService) -> DocumentLifecycle:
    return TrackedLifecycle(svc)


def index_changed(svc: QueryService) -> None:
    if svc.retrieval_cache:
        svc.retrieval_cache.on_index_update()
