"""Query execution and streaming helpers for QueryService."""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import run_query as agent_run_query
from agentic_graphrag.api.errors import BUDGET_EXCEEDED, INTERNAL_ERROR, ApiError
from agentic_graphrag.api.schemas import QueryRequest, QueryResultData
from agentic_graphrag.api.service_helpers import (
    _chain_to_data,
    build_executor_for_service,
    build_llm_for_service,
    cost_units_for_chain,
)
from agentic_graphrag.api.sse import EVENT_ANSWER, EVENT_CACHE_HIT, EVENT_ERROR, EVENT_TRIAGE
from agentic_graphrag.generation.trace import ReasoningChain
from agentic_graphrag.llm.budget import BudgetExceeded
from agentic_graphrag.observability.metrics import QueryMetrics, get_metrics
from agentic_graphrag.observability.trace import get_tracer, span

if TYPE_CHECKING:
    from agentic_graphrag.api.service import QueryService

MS_PER_SECOND = 1000
QUESTION_SPAN_PREVIEW = 120


def execute_run_query(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
) -> QueryResultData:
    """Run a full query with cache, budget, audit, and metrics."""
    t0 = time.perf_counter()
    cached = _try_answer_cache(svc, req)
    if cached is not None:
        return cached
    _reserve_budget(svc, tenant_id=tenant_id, user_id=user_id)
    chain = _invoke_agent(svc, req, tenant_id=tenant_id, user_id=user_id)
    _finalize_chain(chain, t0=t0, req=req, tenant_id=tenant_id, user_id=user_id)
    _persist_and_commit(svc, req, chain, tenant_id=tenant_id, user_id=user_id)
    _record_metrics(chain, tenant_id=tenant_id, user_id=user_id)
    return _chain_to_data(chain)


def stream_query_events(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield SSE (event, payload) pairs for progressive UI (P3-PERF-06)."""
    cache_events = _stream_cache_hit(svc, req)
    if cache_events is not None:
        yield from cache_events
        return
    yield from _stream_triage(svc, req)
    yield from _stream_run(svc, req, tenant_id=tenant_id, user_id=user_id)


def _try_answer_cache(svc: QueryService, req: QueryRequest) -> QueryResultData | None:
    if not (svc.enable_cache and svc.retrieval_cache and not req.force_agentic):
        return None
    hit = svc.retrieval_cache.get_answer(req.question)
    if hit is None:
        return None
    data = QueryResultData.model_validate(hit)
    data.metadata = {**(data.metadata or {}), "cache_hit": True}
    return data


def _budget_api_error(exc: BudgetExceeded) -> ApiError:
    get_metrics().record_budget_trip()
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
            tenant_id=tenant_id, user_id=user_id, estimated_calls=1
        )
    except BudgetExceeded as exc:
        raise _budget_api_error(exc) from exc


def _invoke_agent(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
) -> ReasoningChain:
    # ceil so sub-second timeouts (e.g. 500ms) become 1s, never 0 (0 disables).
    timeout_override = (
        max(1, math.ceil(req.timeout_ms / MS_PER_SECOND))
        if req.timeout_ms is not None
        else None
    )
    guard_cfg = GuardrailConfig.from_app_config(
        svc.cfg, max_hops=req.max_hops, query_timeout_seconds=timeout_override
    )
    budget = (
        svc.multi_budget.query_tracker() if svc.multi_budget else guard_cfg.budget_tracker()
    )
    trace_ctx = get_tracer().start(tenant_id=tenant_id, user_id=user_id)
    with svc._lock:
        executor = build_executor_for_service(
            bundle=svc.bundle,
            cfg=svc.cfg,
            settings=svc.settings,
            allow_llm=svc.allow_llm,
            known_entities=svc.known_entities,
            retrieval_cache=svc.retrieval_cache,
            enable_cache=svc.enable_cache,
        )
        llm = build_llm_for_service(
            allow_llm=svc.allow_llm, settings=svc.settings, cfg=svc.cfg, budget=budget
        )
        return _run_agent_locked(
            svc, req, executor=executor, llm=llm,
            guard_cfg=guard_cfg, budget=budget, trace_ctx=trace_ctx,
        )


def _run_agent_locked(
    svc: QueryService,
    req: QueryRequest,
    *,
    executor: object,
    llm: object,
    guard_cfg: GuardrailConfig,
    budget: object,
    trace_ctx: object,
) -> ReasoningChain:
    try:
        with span(trace_ctx, "run_query", question=req.question[:QUESTION_SPAN_PREVIEW]):
            return agent_run_query(
                req.question,
                executor,  # type: ignore[arg-type]
                llm if svc.allow_llm else None,  # type: ignore[arg-type]
                guard_cfg=guard_cfg,
                budget=budget,  # type: ignore[arg-type]
                allow_llm=svc.allow_llm,
                force_agentic=req.force_agentic,
                enable_triage=svc.enable_triage and not req.force_agentic,
                known_entities=svc.known_entities,
            )
    except BudgetExceeded as exc:
        raise _budget_api_error(exc) from exc
    except Exception as exc:  # noqa: BLE001 — map to safe envelope
        raise ApiError(
            INTERNAL_ERROR,
            "Query failed",
            status_code=500,
            details={"type": type(exc).__name__},
        ) from exc


def _finalize_chain(
    chain: ReasoningChain, *, t0: float, req: QueryRequest, tenant_id: str, user_id: str
) -> None:
    if req.force_agentic:
        chain.metadata = {**(chain.metadata or {}), "force_agentic": True}
    chain.cost.latency_ms = int((time.perf_counter() - t0) * MS_PER_SECOND)
    chain.metadata = {
        **(chain.metadata or {}),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "query_id": chain.query_id,
    }


def _persist_and_commit(
    svc: QueryService,
    req: QueryRequest,
    chain: ReasoningChain,
    *,
    tenant_id: str,
    user_id: str,
) -> None:
    if svc.audit_store is not None:
        try:
            svc.audit_store.save(chain)
        except Exception:
            pass
    if svc.enable_cache and svc.retrieval_cache is not None:
        try:
            svc.retrieval_cache.set_answer(req.question, chain.model_dump(mode="json"))
        except Exception:
            pass
    if svc.multi_budget:
        svc.multi_budget.commit(
            tenant_id=tenant_id,
            user_id=user_id,
            llm_calls=chain.cost.llm_calls,
            tokens=chain.cost.tokens,
            cost_units=cost_units_for_chain(chain.cost.llm_calls),
        )


def _record_metrics(chain: ReasoningChain, *, tenant_id: str, user_id: str) -> None:
    get_metrics().record(
        QueryMetrics(
            query_id=chain.query_id,
            route=chain.route,
            hops=len(chain.steps),
            llm_calls=chain.cost.llm_calls,
            tokens=chain.cost.tokens,
            tool_calls=sum(len(s.tool_calls) for s in chain.steps),
            latency_ms=chain.cost.latency_ms,
            status=chain.status.value if chain.status else "",
            tenant_id=tenant_id,
            user_id=user_id,
        )
    )


def _stream_cache_hit(
    svc: QueryService, req: QueryRequest
) -> list[tuple[str, dict[str, Any]]] | None:
    if not (svc.enable_cache and svc.retrieval_cache and not req.force_agentic):
        return None
    hit = svc.retrieval_cache.get_answer(req.question)
    if hit is None:
        return None
    return [(EVENT_CACHE_HIT, {"query_id": hit.get("query_id")}), (EVENT_ANSWER, hit)]


def _stream_triage(
    svc: QueryService, req: QueryRequest
) -> Iterator[tuple[str, dict[str, Any]]]:
    from agentic_graphrag.agent.triage import triage

    decision = triage(
        req.question, None, allow_llm=False,
        force_agentic=req.force_agentic, known_entities=svc.known_entities,
    )
    yield EVENT_TRIAGE, decision.model_dump(mode="json")


def _stream_run(
    svc: QueryService, req: QueryRequest, *, tenant_id: str, user_id: str
) -> Iterator[tuple[str, dict[str, Any]]]:
    try:
        data = execute_run_query(svc, req, tenant_id=tenant_id, user_id=user_id)
        for step in data.steps:
            yield "sub_question", {"hop": step.hop, "sub_question": step.sub_question}
            yield "hop_done", {
                "hop": step.hop,
                "conclusion": step.conclusion,
                "critic_action": step.critic_action,
            }
        yield EVENT_ANSWER, data.model_dump(mode="json")
    except ApiError as exc:
        yield EVENT_ERROR, {"code": exc.code, "message": exc.message}
    except Exception as exc:  # noqa: BLE001
        yield EVENT_ERROR, {"code": INTERNAL_ERROR, "message": type(exc).__name__}
