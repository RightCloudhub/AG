"""Answer-cache read/write for the query use case (tenant/user scoped)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from agentic_graphrag.app.contracts import QueryRequest, QueryResultData
from agentic_graphrag.app.evidence_sources import catalog_visible
from agentic_graphrag.app.query_persistence import persist_chain, require_audit
from agentic_graphrag.core.reasoning import CostStats, QueryStatus, ReasoningChain
from agentic_graphrag.governance.execution import current_execution
from agentic_graphrag.observability.degradation import (
    DEGRADED_CHANNELS_KEY,
    LOW_CONFIDENCE_KEY,
    REASON_CACHE_WRITE_FAILED,
    record_degradation,
)

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def _try_answer_cache(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
) -> QueryResultData | None:
    from agentic_graphrag.app.wiring import _chain_to_data

    chain = cached_chain(svc, req, tenant_id=tenant_id, user_id=user_id)
    return _chain_to_data(chain) if chain is not None else None


def cached_chain(
    svc: QueryService, req: QueryRequest, *, tenant_id: str, user_id: str
) -> ReasoningChain | None:
    if not (svc.enable_cache and svc.retrieval_cache and not req.force_agentic):
        return None
    if req.strategy == "global" or req.doc_ids is not None:
        return None
    hit = svc.retrieval_cache.get_answer(
        req.question,
        tenant_id=tenant_id,
        user_id=user_id,
        max_hops=req.max_hops,
        force_agentic=req.force_agentic,
        timeout_ms=req.timeout_ms,
    )
    if hit is None:
        return None
    chain = ReasoningChain.model_validate(hit)
    if not _valid_cached_chain(svc, req, chain, tenant_id=tenant_id):
        return None
    original_id = chain.query_id
    execution = current_execution.get()
    chain.query_id = execution.query_id if execution is not None else str(uuid4())
    chain.cost = CostStats()
    chain.metadata.update(
        cache_hit=True,
        original_query_id=original_id,
        query_id=chain.query_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    persist_chain(svc, chain)
    require_audit(req, chain)
    return chain


def _valid_cached_chain(
    svc: QueryService, req: QueryRequest, chain: ReasoningChain, *, tenant_id: str
) -> bool:
    meta = chain.metadata
    if meta.get("tenant_id", "default") != tenant_id or meta.get("kb_id", "default") != req.kb_id:
        return False
    if meta.get("requested_strategy", "auto") != req.strategy:
        return False
    if not catalog_visible(svc, meta.get("evidence", []), current=True):
        return False
    return (
        svc.audit_store is None
        or svc.audit_store.get_for_tenant(chain.query_id, tenant_id) is not None
    )


def _is_cacheable_answer(chain: ReasoningChain) -> bool:
    """Only fully answered, non-degraded results may enter the answer cache."""
    if chain.status != QueryStatus.ANSWERED:
        return False
    if not (chain.answer or "").strip():
        return False
    meta = chain.metadata or {}
    # Even a single degraded channel can leave incomplete evidence or fallback
    # text that must be recomputed after recovery, regardless of low_confidence.
    if (
        meta.get("llm_degraded")
        or meta.get("rerank_degraded")
        or meta.get(LOW_CONFIDENCE_KEY)
        or meta.get(DEGRADED_CHANNELS_KEY)
    ):
        return False
    return True


def _maybe_cache_answer(
    svc: QueryService,
    req: QueryRequest,
    chain: ReasoningChain,
    *,
    tenant_id: str,
    user_id: str,
) -> None:
    """Cache successful answers only (skip no/partial/degraded answers)."""
    if not _is_cacheable_answer(chain):
        return
    assert svc.retrieval_cache is not None
    payload = chain.model_dump(mode="json")
    try:
        svc.retrieval_cache.set_answer(
            req.question,
            payload,
            tenant_id=tenant_id,
            user_id=user_id,
            max_hops=req.max_hops,
            force_agentic=req.force_agentic,
            timeout_ms=req.timeout_ms,
        )
    except Exception as exc:
        # Losing the cache write only costs the next identical query.
        record_degradation("answer_cache", REASON_CACHE_WRITE_FAILED, exc)
