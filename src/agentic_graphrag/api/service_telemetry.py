"""Shared query-service telemetry helpers."""

from agentic_graphrag.generation.trace import ReasoningChain
from agentic_graphrag.observability.metrics import QueryMetrics, get_metrics


def record_metrics(chain: ReasoningChain, *, tenant_id: str, user_id: str) -> None:
    get_metrics().record(
        QueryMetrics(
            query_id=chain.query_id,
            route=chain.route,
            hops=len(chain.steps),
            llm_calls=chain.cost.llm_calls,
            tokens=chain.cost.tokens,
            tool_calls=sum(len(step.tool_calls) for step in chain.steps),
            latency_ms=chain.cost.latency_ms,
            status=chain.status.value if chain.status else "",
            tenant_id=tenant_id,
            user_id=user_id,
        )
    )
