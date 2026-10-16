"""Answer-cache read/write for the query use case (tenant/user scoped)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic_graphrag.app.contracts import QueryRequest, QueryResultData
from agentic_graphrag.core.reasoning import QueryStatus, ReasoningChain

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def _try_answer_cache(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
) -> QueryResultData | None:
    if not (svc.enable_cache and svc.retrieval_cache and not req.force_agentic):
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
    # Defense-in-depth: never return another tenant's cached payload.
    meta = hit.get("metadata") if isinstance(hit, dict) else None
    if isinstance(meta, dict) and meta.get("tenant_id") not in (None, tenant_id):
        return None
    data = QueryResultData.model_validate(hit)
    data.metadata = {**(data.metadata or {}), "cache_hit": True}
    return data


def _is_cacheable_answer(chain: ReasoningChain) -> bool:
    """Only fully answered, non-degraded results may enter the answer cache."""
    if chain.status != QueryStatus.ANSWERED:
        return False
    if not (chain.answer or "").strip():
        return False
    meta = chain.metadata or {}
    if meta.get("llm_degraded"):
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
    except Exception:
        pass
