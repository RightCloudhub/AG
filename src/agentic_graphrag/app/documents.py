"""Document lifecycle wiring and read-cache invalidation."""

from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.knowledge.document_lifecycle import DocumentLifecycle


def lifecycle_for(svc: QueryService) -> DocumentLifecycle:
    return DocumentLifecycle(
        svc.bundle.docs,
        vector=svc.bundle.vector,
        fulltext=svc.bundle.fulltext,
        graph=svc.bundle.graph,
    )


def index_changed(svc: QueryService) -> None:
    if svc.retrieval_cache:
        svc.retrieval_cache.on_index_update()
