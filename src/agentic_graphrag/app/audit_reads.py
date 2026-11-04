"""Audit lookups retain receipts while withdrawing deleted source content."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from agentic_graphrag.app.evidence_sources import catalog_visible

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def visible_query_audit(svc: QueryService, row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") or {}
    if catalog_visible(svc, metadata.get("evidence") or [], current=False):
        return row
    result = deepcopy(row)
    result.update(
        answer="A cited document was deleted; this answer has been withdrawn.",
        claims=[],
        steps=[],
        explored_paths=[],
        missing_info=[],
        status="no_answer",
    )
    # Keep receipt identity and cost, not extracted facts embedded in execution metadata.
    result["metadata"] = {
        key: metadata[key]
        for key in ("tenant_id", "kb_id", "query_id", "audit_status", "snapshot")
        if key in metadata
    }
    result["metadata"].update(source_retracted=True, evidence=[])
    return result
