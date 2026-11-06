"""Agent invocation helpers for QueryService (keeps service_query under size budget)."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import TYPE_CHECKING

from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import run_query as agent_run_query
from agentic_graphrag.agent.options import QueryOptions
from agentic_graphrag.app.contracts import QueryRequest
from agentic_graphrag.app.errors import INTERNAL_ERROR, SERVICE_UNAVAILABLE, ApiError
from agentic_graphrag.app.global_query import run_global_query, uses_global
from agentic_graphrag.app.knowledge_scope import needs_llm_planning
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.budget import BudgetExceeded, BudgetTracker
from agentic_graphrag.governance.execution import ExecutionStopped, current_execution
from agentic_graphrag.observability.context import query_context, submit_with_context
from agentic_graphrag.observability.trace import TraceContext, span

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService

_AGENT_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="agr-query")

_TRANSPORT_TYPES = frozenset(
    {
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "ConnectError",
        "TimeoutException",
        "RemoteProtocolError",
    }
)


def run_agent_with_timeout(
    svc: QueryService,
    req: QueryRequest,
    *,
    executor: Executor,
    llm: LLMClient | None,
    guard_cfg: GuardrailConfig,
    budget: BudgetTracker,
    trace_ctx: TraceContext,
    budget_error_factory: Callable[[BudgetExceeded], ApiError],
    tenant_id: str = "",
) -> ReasoningChain:
    """Hard wall-clock timeout around the agent (hop checks alone cannot cancel LLM I/O)."""
    timeout_s = float(guard_cfg.query_timeout_seconds or 0)
    fut = submit_with_context(
        _AGENT_POOL,
        run_agent,
        svc,
        req,
        executor=executor,
        llm=llm,
        guard_cfg=guard_cfg,
        budget=budget,
        trace_ctx=trace_ctx,
        budget_error_factory=budget_error_factory,
        tenant_id=tenant_id,
    )
    if control := current_execution.get():
        control.pending = fut
    try:
        if timeout_s > 0:
            return fut.result(timeout=timeout_s)
        return fut.result()
    except FuturesTimeout as exc:
        if control is not None:
            control.cancelled.set()
        raise ApiError(
            SERVICE_UNAVAILABLE,
            f"Query timed out after {timeout_s:.0f}s",
            status_code=504,
            details={"reason": "query_timeout", "timeout_seconds": timeout_s},
        ) from exc


def run_agent(
    svc: QueryService,
    req: QueryRequest,
    *,
    executor: Executor,
    llm: LLMClient | None,
    guard_cfg: GuardrailConfig,
    budget: BudgetTracker,
    trace_ctx: TraceContext,
    budget_error_factory: Callable[[BudgetExceeded], ApiError],
    tenant_id: str = "",
) -> ReasoningChain:
    with query_context(query_id=trace_ctx.query_id, tenant_id=tenant_id, user_id=trace_ctx.user_id):
        try:
            with span(trace_ctx, "run_query", question_chars=len(req.question)):
                return _run_selected(
                    svc,
                    req,
                    executor=executor,
                    llm=llm,
                    guard_cfg=guard_cfg,
                    budget=budget,
                    trace_ctx=trace_ctx,
                    tenant_id=tenant_id,
                )
        except ExecutionStopped as exc:
            raise ApiError(SERVICE_UNAVAILABLE, str(exc), status_code=504) from exc
        except BudgetExceeded as exc:
            raise budget_error_factory(exc) from exc
        except Exception as exc:  # noqa: BLE001 — map to safe envelope
            raise map_query_exception(exc) from exc


def _run_selected(
    svc: QueryService,
    req: QueryRequest,
    *,
    executor: Executor,
    llm: LLMClient | None,
    guard_cfg: GuardrailConfig,
    budget: BudgetTracker,
    trace_ctx: TraceContext,
    tenant_id: str,
) -> ReasoningChain:
    if uses_global(svc, req):
        assert llm is not None
        return run_global_query(svc, req, llm=llm, budget=budget, query_id=trace_ctx.query_id)
    return agent_run_query(
        req.question,
        executor,
        llm if svc.allow_llm else None,
        options=QueryOptions(
            query_id=trace_ctx.query_id,
            guard_cfg=guard_cfg,
            budget=budget,
            allow_llm=svc.allow_llm,
            force_agentic=req.force_agentic or needs_llm_planning(svc),
            enable_triage=svc.enable_triage
            and not req.force_agentic
            and not needs_llm_planning(svc),
            known_entities=executor.known_entities,
            tenant_id=tenant_id,
            agent_config=svc.cfg.agent,
        ),
    )


def map_query_exception(exc: BaseException) -> ApiError:
    """Prefer 503 for upstream LLM transport failures over opaque 500."""
    name = type(exc).__name__
    if name == "PlanningUnavailable":
        return ApiError(
            "PLANNING_UNAVAILABLE", "No valid LLM plan could be generated", status_code=503
        )
    msg = str(exc).lower()
    transport = name in _TRANSPORT_TYPES or "timeout" in name.lower()
    if transport or "handshake operation timed out" in msg:
        return ApiError(
            SERVICE_UNAVAILABLE,
            "LLM upstream unavailable or timed out",
            status_code=503,
            details={"type": name},
        )
    if name == "RuntimeError" and "circuit open" in msg:
        return ApiError(
            SERVICE_UNAVAILABLE,
            "LLM circuit open",
            status_code=503,
            details={"type": name},
        )
    return ApiError(
        INTERNAL_ERROR,
        "Query failed",
        status_code=500,
        details={"type": name},
    )
