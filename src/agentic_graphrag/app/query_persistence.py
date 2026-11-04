"""Finalize source lineage and an explicit audit receipt before returning success."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic_graphrag.app.contracts import QueryRequest
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.app.evidence_sources import catalog_visible, publish_chain_sources
from agentic_graphrag.app.telemetry import save_chain_audit
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.observability.degradation import record_degradation

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def persist_chain(svc: QueryService, chain: ReasoningChain) -> None:
    sources_saved = True
    try:
        sources_saved = publish_chain_sources(svc, chain)
    except Exception as exc:
        record_degradation("source_audit", "audit_write_failed", exc)
        sources_saved = False
    catalog = chain.metadata.get("evidence") or []
    if not catalog_visible(svc, catalog, current=False):
        chain.steps = []
        chain.explored_paths = []
        chain.honest_fallback("A cited document was deleted during the query")
        chain.claims = []
        chain.metadata = {
            key: chain.metadata[key]
            for key in ("tenant_id", "kb_id", "snapshot", "model_revisions", "prompt_versions")
            if key in chain.metadata
        }
        chain.metadata.update(evidence=[], source_retracted=True)
        sources_saved = False
    chain.metadata["audit_status"] = (
        "persisted" if svc.audit_store and svc.audit_store.path else "disabled"
    )
    if not sources_saved:
        chain.metadata["audit_status"] = "failed"
    saved = save_chain_audit(svc.audit_store, chain)
    if svc.audit_store is not None and not saved:
        chain.metadata["audit_status"] = "failed"


def require_audit(req: QueryRequest, chain: ReasoningChain) -> None:
    if req.audit_required and chain.metadata.get("audit_status") != "persisted":
        raise ApiError(
            "AUDIT_UNAVAILABLE",
            "Durable audit could not be recorded",
            status_code=503,
            details={"query_id": chain.query_id, "cost": chain.cost.model_dump(mode="json")},
        )
