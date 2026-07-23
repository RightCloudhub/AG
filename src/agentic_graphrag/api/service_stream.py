"""Live SSE query streaming (true incremental hops — P3-PERF-06)."""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop_stream import (
    EVENT_FINAL_CHAIN,
    AgentStreamEmptyError,
    iter_query_progress,
)
from agentic_graphrag.agent.options import QueryOptions
from agentic_graphrag.api.errors import INTERNAL_ERROR, ApiError
from agentic_graphrag.api.schemas import QueryRequest
from agentic_graphrag.api.service_helpers import (
    build_executor_for_service,
    build_llm_for_service,
    stream_cache_hit_events,
)
from agentic_graphrag.api.service_query import (
    MS_PER_SECOND,
    QUESTION_SPAN_PREVIEW,
    _budget_api_error,
    _finalize_chain,
    _persist_and_commit,
    _record_metrics,
    _reserve_budget,
)
from agentic_graphrag.api.sse import EVENT_ANSWER, EVENT_ERROR
from agentic_graphrag.generation.trace import ReasoningChain
from agentic_graphrag.llm.budget import BudgetExceeded
from agentic_graphrag.observability.trace import get_tracer, span

if TYPE_CHECKING:
    from agentic_graphrag.agent.executor import Executor
    from agentic_graphrag.api.service import QueryService
    from agentic_graphrag.llm.provider import LLMProvider


def stream_query_events(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield SSE (event, payload) pairs with live hop progress."""
    cache_events = stream_cache_hit_events(svc, req)
    if cache_events is not None:
        yield from cache_events
        return
    yield from _stream_live(svc, req, tenant_id=tenant_id, user_id=user_id)


def _stream_live(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
) -> Iterator[tuple[str, dict[str, Any]]]:
    t0 = time.perf_counter()
    try:
        _reserve_budget(svc, tenant_id=tenant_id, user_id=user_id)
        yield from _stream_agent(svc, req, tenant_id=tenant_id, user_id=user_id, t0=t0)
    except ApiError as exc:
        yield EVENT_ERROR, {"code": exc.code, "message": exc.message}
    except BudgetExceeded as exc:
        err = _budget_api_error(exc)
        yield EVENT_ERROR, {"code": err.code, "message": err.message}
    except AgentStreamEmptyError as exc:
        yield EVENT_ERROR, {"code": INTERNAL_ERROR, "message": type(exc).__name__}
    except Exception as exc:  # noqa: BLE001
        yield EVENT_ERROR, {"code": INTERNAL_ERROR, "message": type(exc).__name__}


def _stream_agent(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
    t0: float,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Build deps and run agent under the same service lock as non-stream path."""
    guard_cfg, budget = _guard_and_budget(svc, req)
    trace_ctx = get_tracer().start(tenant_id=tenant_id, user_id=user_id)
    # Hold lock for prepare + agent stream (matches execute_run_query).
    with svc._lock:
        executor, llm, opts = _build_locked_deps(svc, req, (guard_cfg, budget))
        with span(trace_ctx, "stream_query", question=req.question[:QUESTION_SPAN_PREVIEW]):
            yield from _consume_progress(
                svc,
                req,
                executor=executor,
                llm=llm,
                opts=opts,
                tenant_id=tenant_id,
                user_id=user_id,
                t0=t0,
            )


def _guard_and_budget(svc: QueryService, req: QueryRequest) -> tuple[GuardrailConfig, Any]:
    timeout_override = (
        max(1, math.ceil(req.timeout_ms / MS_PER_SECOND)) if req.timeout_ms is not None else None
    )
    guard_cfg = GuardrailConfig.from_app_config(
        svc.cfg, max_hops=req.max_hops, query_timeout_seconds=timeout_override
    )
    budget = svc.multi_budget.query_tracker() if svc.multi_budget else guard_cfg.budget_tracker()
    return guard_cfg, budget


def _build_locked_deps(
    svc: QueryService,
    req: QueryRequest,
    guard_budget: tuple[GuardrailConfig, Any],
) -> tuple[Executor, LLMProvider | None, QueryOptions]:
    guard_cfg, budget = guard_budget
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
    opts = QueryOptions(
        guard_cfg=guard_cfg,
        budget=budget,
        allow_llm=svc.allow_llm,
        force_agentic=req.force_agentic,
        enable_triage=svc.enable_triage and not req.force_agentic,
        known_entities=svc.known_entities,
    )
    return executor, llm, opts


def _consume_progress(
    svc: QueryService,
    req: QueryRequest,
    *,
    executor: Executor,
    llm: LLMProvider | None,
    opts: QueryOptions,
    tenant_id: str,
    user_id: str,
    t0: float,
) -> Iterator[tuple[str, dict[str, Any]]]:
    for etype, payload in iter_query_progress(
        req.question, executor, llm if svc.allow_llm else None, options=opts
    ):
        if etype != EVENT_FINAL_CHAIN:
            yield etype, payload
            continue
        yield from _finish_answer(svc, req, payload, tenant_id=tenant_id, user_id=user_id, t0=t0)


def _finish_answer(
    svc: QueryService,
    req: QueryRequest,
    payload: Any,
    *,
    tenant_id: str,
    user_id: str,
    t0: float,
) -> Iterator[tuple[str, dict[str, Any]]]:
    chain = (
        payload if isinstance(payload, ReasoningChain) else ReasoningChain.model_validate(payload)
    )
    _finalize_chain(chain, t0=t0, req=req, tenant_id=tenant_id, user_id=user_id)
    _persist_and_commit(svc, req, chain, tenant_id=tenant_id, user_id=user_id)
    _record_metrics(chain, tenant_id=tenant_id, user_id=user_id)
    yield EVENT_ANSWER, chain.model_dump(mode="json")
