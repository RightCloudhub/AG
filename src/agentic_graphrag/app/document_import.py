"""Stage a CLI document batch using the same scoped lifecycle as HTTP uploads."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agentic_graphrag.app.documents import lifecycle_for
from agentic_graphrag.app.knowledge_scope import select_service
from agentic_graphrag.app.knowledge_writes import prepare_upload
from agentic_graphrag.core.ports.stores import DocumentRecord

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def stage_documents(
    svc: QueryService,
    documents: list[DocumentRecord],
    *,
    tenant_id: str,
    kb_id: str,
    operation_id: str,
) -> list[DocumentRecord]:
    selected = select_service(svc, tenant_id=tenant_id, kb_id=kb_id)
    assert selected.ingest_tasks is not None
    existing = selected.ingest_tasks.get(operation_id)
    selected = prepare_upload(selected, operation_id)
    assert selected.ingest_tasks is not None
    rows = existing.docs if existing else []
    if existing is None:
        lifecycle = lifecycle_for(selected)
        for doc in documents:
            doc.tenant_id, doc.kb_id = tenant_id, kb_id
            if source_path := doc.metadata.get("source_path"):
                doc.source_uri = Path(source_path).resolve().as_uri()
            rows.append(lifecycle.accept(doc))
        selected.ingest_tasks.create(tenant_id, rows, task_id=operation_id)
    return [
        stored
        for row in rows
        if (stored := selected.bundle.docs.get(row["doc_id"], tenant_id=tenant_id)) is not None
    ]
