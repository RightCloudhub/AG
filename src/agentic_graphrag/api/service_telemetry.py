"""Shared query-service telemetry helpers."""

import logging
from typing import Any

from agentic_graphrag.generation.trace import ReasoningChain
from agentic_graphrag.observability.metrics import QueryMetrics, get_metrics

logger = logging.getLogger(__name__)


def save_chain_audit(audit_store: Any, chain: ReasoningChain) -> bool:
    """Persist a chain to the audit store; log (never raise) on failure.

    A bare ``except: pass`` here let compliance records vanish silently — the
    query looked successful with no audit trail behind it (BL-10).
    """
    if audit_store is None:
        return False
    try:
        audit_store.save(chain)
    except Exception:
        logger.error("Audit save failed for query %s", chain.query_id, exc_info=True)
        return False
    return True


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
