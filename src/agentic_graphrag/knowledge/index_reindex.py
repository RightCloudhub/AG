"""Tenant embedding rebuild: a model switch publishes only a complete vector space."""

from __future__ import annotations

from agentic_graphrag.core.ports.capabilities import VectorModelRebuild
from agentic_graphrag.core.ports.stores import DocStore, VectorStore
from agentic_graphrag.knowledge.index_pipeline import IndexPipeline
from agentic_graphrag.knowledge.index_result import IngestResult


def reindex_tenant(
    pipeline: IndexPipeline, *, docs: DocStore, vector: VectorStore, model: str, tenant_id: str
) -> list[IngestResult]:
    if pipeline.embed_many is None:
        raise ValueError("Reindex requires an embedder")
    with docs.lock():
        rebuild = vector if isinstance(vector, VectorModelRebuild) else None
        if rebuild:
            rebuild.begin_reindex(model, tenant_id=tenant_id)
        try:
            results = [
                pipeline.process(did, tenant_id=tenant_id, reindex=True)
                for did in docs.list_ids(tenant_id=tenant_id)
            ]
            # An unchunkable document has no vectors to lose, so it cannot hold back the switch.
            if rebuild and not any(r.error and not r.unchunkable for r in results):
                rebuild.commit_reindex()
            return results
        finally:
            if rebuild:
                rebuild.end_reindex()
