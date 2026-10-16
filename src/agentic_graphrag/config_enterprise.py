"""Enterprise configuration models."""

from pydantic import BaseModel


class TenantBudgetConfig(BaseModel):
    """Per-tenant rate limit and budget overrides (ENT-05)."""

    qps: float | None = None
    concurrent: int | None = None
    max_llm_calls: int | None = None
    max_tokens: int | None = None
    max_cost_units: float | None = None


class RetentionConfig(BaseModel):
    """Data retention periods in days (ENT-06)."""

    audit_chains_days: int = 90
    audit_events_days: int = 90
    review_queue_days: int = 30
    ingest_tasks_days: int = 30
