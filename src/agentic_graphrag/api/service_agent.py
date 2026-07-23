"""Agent invocation helpers for QueryService (keeps service_query under size budget)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import TYPE_CHECKING, Any

from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import run_query as agent_run_query
from agentic_graphrag.api.errors import INTERNAL_ERROR, SERVICE_UNAVAILABLE, ApiError
from agentic_graphrag.api.schemas import QueryRequest
from agentic_graphrag.generation.trace import ReasoningChain
from agentic_graphrag.llm.budget import BudgetExceeded
from agentic_graphrag.observability.trace import span

if TYPE_CHECKING:
    from agentic_graphrag.api.service import QueryService

QUESTION_SPAN_PREVIEW = 120
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
    executor: object,
    llm: object,
    guard_cfg: GuardrailConfig,
    budget: object,
    trace_ctx: object,
    budget_error_factory: Any,
) -> ReasoningChain:
    """Hard wall-clock timeout around the agent (hop checks alone cannot cancel LLM I/O)."""
    timeout_s = float(guard_cfg.query_timeout_seconds or 0)
    fut = _AGENT_POOL.submit(
        run_agent,
        svc,
        req,
        executor=executor,
        llm=llm,
        guard_cfg=guard_cfg,
        budget=budget,
        trace_ctx=trace_ctx,
        budget_error_factory=budget_error_factory,
    )
    try:
        if timeout_s > 0:
            return fut.result(timeout=timeout_s)
        return fut.result()
    except FuturesTimeout as exc:
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
    executor: object,
    llm: object,
    guard_cfg: GuardrailConfig,
    budget: object,
    trace_ctx: object,
    budget_error_factory: Any,
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
        raise budget_error_factory(exc) from exc
    except Exception as exc:  # noqa: BLE001 — map to safe envelope
        raise map_query_exception(exc) from exc


def map_query_exception(exc: BaseException) -> ApiError:
    """Prefer 503 for upstream LLM transport failures over opaque 500."""
    name = type(exc).__name__
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
