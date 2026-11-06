"""Application boundary for fixed-snapshot drafts and human-approved promotion."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from agentic_graphrag.app.knowledge_scope import knowledge_store
from agentic_graphrag.app.query_selection import select_documents
from agentic_graphrag.config import runtime_dir
from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.core.schema_drafts import DraftApproval, SchemaDraft
from agentic_graphrag.knowledge.generation_pipeline import GenerationPipeline
from agentic_graphrag.knowledge.schema_drafts import draft_from_graph
from agentic_graphrag.knowledge.schema_promotion import SchemaPromoter, approve_draft
from agentic_graphrag.stores.schema_registry import SQLiteSchemaRegistry

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


@contextmanager
def registry_for(svc: QueryService) -> Iterator[SQLiteSchemaRegistry]:
    registry = SQLiteSchemaRegistry(runtime_dir(svc.cfg) / "schemas.sqlite3")
    try:
        yield registry
    finally:
        registry.close()


def create_draft(svc: QueryService, *, aliases: dict[str, str]) -> SchemaDraft:
    kb = svc.knowledge_base
    assert kb is not None
    selected = select_documents(svc, None, tenant_id=kb.tenant_id)
    draft = draft_from_graph(selected.bundle.graph, kb, aliases=aliases)
    with registry_for(svc) as registry:
        return registry.save_draft(draft)


def approve_schema(svc: QueryService, draft_id: str, *, approval: DraftApproval) -> SchemaDraft:
    kb = svc.knowledge_base
    assert kb is not None
    with registry_for(svc) as registry:
        return approve_draft(registry, kb.scope, draft_id, approval=approval)


def promote_schema(svc: QueryService, draft_id: str, *, operation_id: str) -> KnowledgeBase:
    kb = svc.knowledge_base
    assert kb is not None
    root = svc.parent or svc
    with registry_for(svc) as registry:
        promoter = SchemaPromoter(GenerationPipeline(root.bundle, knowledge_store(root)), registry)
        published = promoter.promote(kb.scope, draft_id, operation_id=operation_id)
    if root.retrieval_cache is not None:
        root.retrieval_cache.on_index_update()
    return published
