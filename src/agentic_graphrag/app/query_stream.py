"""Live SSE query streaming (true incremental hops — P3-PERF-06)."""

from __future__ import annotations

import time
import typing
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop_stream import (
    EVENT_FINAL_CHAIN,
    AgentStreamEmptyError,
    iter_query_progress,
)
from agentic_graphrag.agent.options import QueryOptions
from agentic_graphrag.app.contracts import QueryRequest
from agentic_graphrag.app.degradation_policy import (
    DEGRADED_CHANNELS_KEY,
    LOW_CONFIDENCE_KEY,
    apply_degradation_policy,
)
from agentic_graphrag.app.errors import INTERNAL_ERROR, ApiError
from agentic_graphrag.app.global_query import global_progress, uses_global
from agentic_graphrag.app.query_accounting import close_client, settle_aborted
from agentic_graphrag.app.query_limits import guard_and_budget as _guard_and_budget
from agentic_graphrag.app.query_run import (
    QUESTION_SPAN_PREVIEW,
    _budget_api_error,
    _finalize_chain,
    _persist_and_commit,
    _release_budget,
    _reserve_budget,
)
from agentic_graphrag.app.stream_events import EVENT_ANSWER, EVENT_DEGRADED, EVENT_ERROR
from agentic_graphrag.app.telemetry import record_metrics as _record_metrics
from agentic_graphrag.app.wiring import (
    build_llm_for_service,
    stream_cache_hit_events,
)
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.budget import BudgetExceeded
from agentic_graphrag.governance.execution import (
    ExecutionStopped,
    current_execution,
    execution_scope,
)
from agentic_graphrag.observability.context import iterate_with_context, query_context
from agentic_graphrag.observability.degradation import (
    REASON_UNEXPECTED,
    ChannelStatus,
    record_degradation,
)
from agentic_graphrag.observability.logging_setup import request_id_var
from agentic_graphrag.observability.trace import TraceContext, get_tracer, span

if TYPE_CHECKING:
    from agentic_graphrag.agent.executor import Executor
    from agentic_graphrag.app.query_service import QueryService
    from agentic_graphrag.core.ports.llm import LLMClient


def stream_query_events(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield SSE (event, payload) pairs with live hop progress."""
    cache_events = stream_cache_hit_events(svc, req, tenant_id=tenant_id, user_id=user_id)
    if cache_events is not None:
        yield from cache_events
        return
    yield from iterate_with_context(_stream_live(svc, req, tenant_id=tenant_id, user_id=user_id))


def _stream_live(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
) -> Iterator[tuple[str, dict[str, Any]]]:
    with execution_scope(reuse=True) as control:
        trace_ctx = get_tracer().start(
            query_id=control.query_id, tenant_id=tenant_id, user_id=user_id
        )
        with query_context(query_id=trace_ctx.query_id, tenant_id=tenant_id, user_id=user_id):
            yield from _stream_with_budget(
                svc, req, tenant_id=tenant_id, user_id=user_id, trace_ctx=trace_ctx
            )


def _stream_with_budget(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
    trace_ctx: TraceContext,
) -> Iterator[tuple[str, dict[str, Any]]]:
    t0 = time.perf_counter()
    reserved = False
    try:
        _reserve_budget(svc, tenant_id=tenant_id, user_id=user_id)
        reserved = True
        for etype, payload in _stream_agent(
            svc, req, tenant_id=tenant_id, user_id=user_id, t0=t0, trace_ctx=trace_ctx
        ):
            if etype == EVENT_ANSWER or payload.pop("_settled", False):
                # _finish_answer settled the reservation via commit.
                reserved = False
            yield etype, payload
    except ApiError as exc:
        yield EVENT_ERROR, {"code": exc.code, "message": exc.message}
    except ExecutionStopped as exc:
        yield EVENT_ERROR, {"code": "QUERY_CANCELLED", "message": str(exc)}
    except BudgetExceeded as exc:
        err = _budget_api_error(exc, tenant_id=tenant_id, user_id=user_id)
        yield EVENT_ERROR, {"code": err.code, "message": err.message}
    except AgentStreamEmptyError as exc:
        yield EVENT_ERROR, {"code": INTERNAL_ERROR, "message": type(exc).__name__}
    except Exception as exc:  # noqa: BLE001
        # The client sees an error frame; before this the server saw nothing at all.
        record_degradation("query_stream", REASON_UNEXPECTED, exc, severity=ChannelStatus.FAILED)
        yield EVENT_ERROR, {"code": INTERNAL_ERROR, "message": type(exc).__name__}
    finally:
        _settle_stream(svc, reserved=reserved, tenant_id=tenant_id, user_id=user_id)


def _settle_stream(svc: QueryService, *, reserved: bool, tenant_id: str, user_id: str) -> None:
    control = current_execution.get()
    if reserved and not (control and control.settled):
        _release_budget(svc, tenant_id=tenant_id, user_id=user_id)
        if control is not None:
            settle_aborted(svc, control, tenant_id=tenant_id, user_id=user_id)
    elif control is not None:
        close_client(control)


def _stream_agent(
    svc: QueryService,
    req: QueryRequest,
    *,
    tenant_id: str,
    user_id: str,
    t0: float,
    trace_ctx: TraceContext,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Build deps and stream agent progress without holding a process-global lock.

    Holding a lock across the full SSE lifetime blocked other queries when a
    slow client drained events slowly. Build is cheap and stores are shared.
    """
    guard_cfg, budget = _guard_and_budget(svc, req)
    if control := current_execution.get():
        control.budget = budget
        control.deadline = (
            time.monotonic() + guard_cfg.query_timeout_seconds
            if guard_cfg.query_timeout_seconds
            else None
        )
    executor, llm, opts = _build_stream_deps(
        svc, req, (guard_cfg, budget), tenant_id=tenant_id, query_id=trace_ctx.query_id
    )
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


def _build_stream_deps(
    svc: QueryService,
    req: QueryRequest,
    guard_budget: tuple[GuardrailConfig, Any],
    *,
    tenant_id: str,
    query_id: str,
) -> tuple[Executor, LLMClient | None, QueryOptions]:
    guard_cfg, budget = guard_budget
    from agentic_graphrag.app.query_dependencies import query_dependencies

    llm = build_llm_for_service(
        allow_llm=svc.allow_llm,
        settings=svc.settings,
        cfg=svc.cfg,
        budget=budget,
        tenant_id=tenant_id,
        kb_id=req.kb_id,
    )
    executor, llm = query_dependencies(svc, budget, tenant_id=tenant_id, llm=llm)
    if control := current_execution.get():
        control.llm = llm
    opts = QueryOptions(
        query_id=query_id,
        guard_cfg=guard_cfg,
        budget=budget,
        allow_llm=svc.allow_llm,
        force_agentic=req.force_agentic or executor.config.llm_only,
        enable_triage=svc.enable_triage and not req.force_agentic and not executor.config.llm_only,
        known_entities=executor.known_entities,
        tenant_id=tenant_id,
    )
    return executor, llm, opts


def _consume_progress(
    svc: QueryService,
    req: QueryRequest,
    *,
    executor: Executor,
    llm: LLMClient | None,
    opts: QueryOptions,
    tenant_id: str,
    user_id: str,
    t0: float,
) -> Iterator[tuple[str, dict[str, Any]]]:
    if uses_global(svc, req):
        assert llm is not None and opts.budget is not None and opts.query_id is not None
        progress = global_progress(svc, req, llm=llm, budget=opts.budget, query_id=opts.query_id)
    else:
        progress = iter_query_progress(
            req.question, executor, llm if svc.allow_llm else None, options=opts
        )
    for etype, payload in progress:
        if etype != EVENT_FINAL_CHAIN:
            yield etype, typing.cast(dict[str, object], payload)
            continue
        chain = _final_chain(payload)
        # O-9: fold the run's channel health into the chain, then tell the client
        # which channels degraded before the answer arrives.
        apply_degradation_policy(chain, executor.health, svc.cfg)
        yield from _degraded_events(chain)
        yield from _finish_answer(svc, req, chain, tenant_id=tenant_id, user_id=user_id, t0=t0)


def _final_chain(payload: Any) -> ReasoningChain:
    if isinstance(payload, ReasoningChain):
        return payload
    return ReasoningChain.model_validate(payload)


def _degraded_events(chain: ReasoningChain) -> Iterator[tuple[str, dict[str, Any]]]:
    """Emit one ``degraded`` frame when the run lost a retrieval channel."""
    channels = chain.metadata.get(DEGRADED_CHANNELS_KEY) or []
    if not channels:
        return
    yield (
        EVENT_DEGRADED,
        {
            "channels": channels,
            "low_confidence": bool(chain.metadata.get(LOW_CONFIDENCE_KEY)),
        },
    )


def _finish_answer(
    svc: QueryService,
    req: QueryRequest,
    chain: ReasoningChain,
    *,
    tenant_id: str,
    user_id: str,
    t0: float,
) -> Iterator[tuple[str, dict[str, Any]]]:
    # Streamed audit records used to carry an empty request_id while the
    # non-streaming path set it (docs/design/BUSINESS_LOGIC.md BL-11).
    _finalize_chain(
        chain,
        t0=t0,
        req=req,
        tenant_id=tenant_id,
        user_id=user_id,
        request_id=request_id_var.get(),
    )
    _persist_and_commit(svc, req, chain, tenant_id=tenant_id, user_id=user_id)
    _record_metrics(chain, tenant_id=tenant_id, user_id=user_id)
    if req.audit_required and chain.metadata.get("audit_status") != "persisted":
        yield (
            EVENT_ERROR,
            {
                "code": "AUDIT_UNAVAILABLE",
                "message": "Durable audit could not be recorded",
                "query_id": chain.query_id,
                "_settled": True,
            },
        )
        return
    yield EVENT_ANSWER, chain.model_dump(mode="json")
