"""Query execution and streaming helpers for QueryService."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from agentic_graphrag.app.answer_cache import (  # noqa: F401 — re-exported
    _is_cacheable_answer,
    _maybe_cache_answer,
    _try_answer_cache,
)
from agentic_graphrag.app.contracts import QueryRequest, QueryResultData
from agentic_graphrag.app.degradation_policy import apply_degradation_policy
from agentic_graphrag.app.errors import BUDGET_EXCEEDED, ApiError
from agentic_graphrag.app.model_metadata import attach_model_metadata
from agentic_graphrag.app.query_accounting import close_client, settle_aborted
from agentic_graphrag.app.query_agent import QUESTION_SPAN_PREVIEW, run_agent_with_timeout
from agentic_graphrag.app.query_limits import guard_and_budget
from agentic_graphrag.app.query_persistence import persist_chain, require_audit
from agentic_graphrag.app.telemetry import record_metrics as _record_metrics
from agentic_graphrag.app.wiring import (
    _chain_to_data,
    build_llm_for_service,
    cost_units_for_chain,
)
from agentic_graphrag.core.constants import MILLISECONDS_PER_SECOND
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance import audit_events
from agentic_graphrag.governance.budget import BudgetExceeded
from agentic_graphrag.governance.execution import current_execution, execution_scope
from agentic_graphrag.observability.context import query_context
from agentic_graphrag.observability.degradation import (
    REASON_AUDIT_WRITE_FAILED,
    ChannelStatus,
    record_degradation,
)
from agentic_graphrag.observability.logging_setup import query_id_var, request_id_var
from agentic_graphrag.observability.metrics import get_metrics
from agentic_graphrag.observability.trace import get_tracer

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService

MS_PER_SECOND = MILLISECONDS_PER_SECOND
# Must match MultiLevelBudget.check_and_reserve defaults used by the API path.
RESERVE_CALLS = 1
RESERVE_TOKENS = 0
RESERVE_COST = 0.01
# Re-export for service_stream / tests.
__all__ = [
    "MS_PER_SECOND",
    "QUESTION_SPAN_PREVIEW",
    "RESERVE_CALLS",
    "RESERVE_COST",
    "RESERVE_TOKENS",
    "execute_query_chain",
    "execute_run_query",
    "stream_query_events",
    "_release_budget",
    "_reserve_budget",
]


def execute_run_query(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
) -> QueryResultData:
    """Run a full query with cache, budget, audit, and metrics."""
    cached = _try_answer_cache(svc, req, tenant_id=tenant_id, user_id=user_id)
    if cached is not None:
        return cached
    chain = execute_query_chain(svc, req, tenant_id=tenant_id, user_id=user_id)
    return _chain_to_data(chain)


def execute_query_chain(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
) -> ReasoningChain:
    """Production query path minus the answer-cache *read*: budget reservation,
    pipeline, finalize, audit, cache write, budget commit, metrics.

    Returns the full :class:`ReasoningChain` for drivers that need step-level
    detail (eval, CLI). The HTTP path is :func:`execute_run_query`.
    """
    with execution_scope() as control:
        with query_context(query_id=control.query_id, tenant_id=tenant_id, user_id=user_id):
            return _execute_query_chain(svc, req, tenant_id=tenant_id, user_id=user_id)


def _execute_query_chain(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
) -> ReasoningChain:
    t0 = time.perf_counter()
    _reserve_budget(svc, tenant_id=tenant_id, user_id=user_id)
    settled = False
    try:
        chain = _invoke_agent(svc, req, tenant_id=tenant_id, user_id=user_id)
        request_id = request_id_var.get()
        _finalize_chain(
            chain,
            t0=t0,
            req=req,
            tenant_id=tenant_id,
            user_id=user_id,
            request_id=request_id,
        )
        _persist_and_commit(svc, req, chain, tenant_id=tenant_id, user_id=user_id)
        settled = True
        _record_metrics(chain, tenant_id=tenant_id, user_id=user_id)
        require_audit(req, chain)
        return chain
    except BaseException:
        if not settled:
            _release_budget(svc, tenant_id=tenant_id, user_id=user_id)
            if control := current_execution.get():
                settle_aborted(svc, control, tenant_id=tenant_id, user_id=user_id)
        raise
    finally:
        if settled and (control := current_execution.get()):
            close_client(control)


def stream_query_events(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield SSE (event, payload) with live hop progress (P3-PERF-06).

    Implementation lives in :mod:`agentic_graphrag.app.query_stream` so this
    module stays under the code-metrics file-size budget.
    """
    from agentic_graphrag.app.query_stream import stream_query_events as _live

    yield from _live(svc, req, tenant_id=tenant_id, user_id=user_id)


def _budget_api_error(exc: BudgetExceeded, *, tenant_id: str = "", user_id: str = "") -> ApiError:
    get_metrics().record_budget_trip(tenant_id=tenant_id)
    try:
        audit_events.emit_audit_event(
            audit_events.BUDGET_EXCEEDED, outcome="denied", tenant_id=tenant_id, user_id=user_id
        )
    # ``audit_error`` must not shadow the BudgetExceeded argument used below.
    except Exception as audit_error:  # noqa: BLE001
        record_degradation(
            "audit_events", REASON_AUDIT_WRITE_FAILED, audit_error, severity=ChannelStatus.FAILED
        )
    return ApiError(
        BUDGET_EXCEEDED,
        "Query budget exceeded",
        status_code=429,
        details={"reason": str(exc)},
    )


def _reserve_budget(svc: QueryService, *, tenant_id: str, user_id: str) -> None:
    if not svc.multi_budget:
        return
    try:
        svc.multi_budget.check_and_reserve(
            tenant_id=tenant_id,
            user_id=user_id,
            estimated_calls=RESERVE_CALLS,
            estimated_tokens=RESERVE_TOKENS,
            estimated_cost=RESERVE_COST,
        )
    except BudgetExceeded as exc:
        raise _budget_api_error(exc, tenant_id=tenant_id, user_id=user_id) from exc


def _release_budget(svc: QueryService, *, tenant_id: str, user_id: str) -> None:
    if not svc.multi_budget:
        return
    svc.multi_budget.release(
        tenant_id=tenant_id,
        user_id=user_id,
        reserved_calls=RESERVE_CALLS,
        reserved_tokens=RESERVE_TOKENS,
        reserved_cost=RESERVE_COST,
    )


def _invoke_agent(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
) -> ReasoningChain:
    guard_cfg, budget = guard_and_budget(svc, req)
    if control := current_execution.get():
        control.budget = budget
    trace_ctx = get_tracer().start(
        query_id=query_id_var.get() or None, tenant_id=tenant_id, user_id=user_id
    )
    # Build deps without a process-global lock so concurrent queries can proceed.
    from agentic_graphrag.app.query_dependencies import query_dependencies

    llm = build_llm_for_service(
        allow_llm=svc.allow_llm,
        settings=svc.settings,
        cfg=svc.cfg,
        budget=budget,
        tenant_id=tenant_id,
        kb_id=req.kb_id,
    )
    if control is not None:
        control.llm = llm
    executor, llm = query_dependencies(svc, budget, tenant_id=tenant_id, llm=llm)
    chain = run_agent_with_timeout(
        svc,
        req,
        executor=executor,
        llm=llm,
        guard_cfg=guard_cfg,
        budget=budget,
        trace_ctx=trace_ctx,
        budget_error_factory=lambda exc: _budget_api_error(
            exc, tenant_id=tenant_id, user_id=user_id
        ),
        tenant_id=tenant_id,
    )
    # O-9: a run that lost retrieval channels must say so in its response.
    apply_degradation_policy(chain, executor.health, svc.cfg)
    get_tracer().rekey(trace_ctx.query_id, chain.query_id)
    return chain


def _finalize_chain(
    chain: ReasoningChain,
    *,
    t0: float,
    req: QueryRequest,
    tenant_id: str,
    user_id: str,
    request_id: str = "",
) -> None:
    if req.force_agentic:
        chain.metadata = {**(chain.metadata or {}), "force_agentic": True}
    chain.cost.latency_ms = int((time.perf_counter() - t0) * MS_PER_SECOND)
    chain.metadata = {
        **(chain.metadata or {}),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "query_id": chain.query_id,
        "request_id": request_id,
        "kb_id": req.kb_id,
        "strategy": chain.metadata.get("strategy", "local"),
        "requested_strategy": req.strategy,
        "doc_ids": req.doc_ids,
    }


def _persist_and_commit(
    svc: QueryService,
    req: QueryRequest,
    chain: ReasoningChain,
    *,
    tenant_id: str,
    user_id: str,
) -> None:
    attach_model_metadata(svc, chain)
    persist_chain(svc, chain)
    if svc.enable_cache and svc.retrieval_cache is not None:
        _maybe_cache_answer(svc, req, chain, tenant_id=tenant_id, user_id=user_id)
    if svc.multi_budget:
        svc.multi_budget.commit(
            tenant_id=tenant_id,
            user_id=user_id,
            llm_calls=chain.cost.llm_calls,
            tokens=chain.cost.tokens,
            cost_units=cost_units_for_chain(chain.cost.llm_calls),
            reserved_calls=RESERVE_CALLS,
            reserved_tokens=RESERVE_TOKENS,
            reserved_cost=RESERVE_COST,
        )
    if control := current_execution.get():
        control.settled = True
