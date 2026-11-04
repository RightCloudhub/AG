"""Shared query-service telemetry helpers."""

from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.audit_store import AuditStore
from agentic_graphrag.governance.budget import LLM_BY_ROLE_KEY
from agentic_graphrag.observability.degradation import (
    REASON_AUDIT_WRITE_FAILED,
    ChannelStatus,
    record_degradation,
)
from agentic_graphrag.observability.metrics import QueryMetrics, get_metrics


def save_chain_audit(audit_store: AuditStore | None, chain: ReasoningChain) -> bool:
    """Persist a chain to the audit store; record (never raise) on failure.

    A bare ``except: pass`` here let compliance records vanish silently — the
    query looked successful with no audit trail behind it (BL-10).
    """
    if audit_store is None:
        return False
    try:
        audit_store.save(chain)
    except Exception as exc:
        record_degradation(
            "audit_store",
            REASON_AUDIT_WRITE_FAILED,
            exc,
            severity=ChannelStatus.FAILED,
            query_id=chain.query_id,
        )
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
            llm_by_role=(chain.metadata or {}).get(LLM_BY_ROLE_KEY) or {},
        )
    )
