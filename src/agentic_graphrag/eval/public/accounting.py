"""Query-scoped usage ledgers and money accounting (B-07).

Unknown usage is ``None`` and is reported with its measurement coverage; it is
never silently counted as zero. Money is computed only from dated manifest prices.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from agentic_graphrag.eval.public.manifest_models import PricePoint

TOKENS_PER_MILLION = 1_000_000
LEDGER_FIELDS: tuple[str, ...] = (
    "prompt_tokens",
    "completion_tokens",
    "llm_calls",
    "embed_calls",
    "embed_tokens",
    "rerank_calls",
    "elapsed_ms",
)
# BudgetTracker snapshot keys → ledger fields. Its ``prompt_tokens`` include query
# embedding tokens (BudgetTracker.record_embedding), so ``embed_tokens`` stays unknown.
_BUDGET_FIELDS: dict[str, str] = {
    "prompt_tokens": "prompt_tokens",
    "completion_tokens": "completion_tokens",
    "llm_calls": "llm_calls",
    "embed_calls": "embed_calls",
}


class UsageLedger(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: str = "query"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    llm_calls: int | None = None
    embed_calls: int | None = None
    embed_tokens: int | None = None
    rerank_calls: int | None = None
    elapsed_ms: float | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None or self.completion_tokens is None:
            return None
        return self.prompt_tokens + self.completion_tokens

    @property
    def spend_known(self) -> bool:
        return self.total_tokens is not None and self.llm_calls is not None


UNKNOWN_USAGE = UsageLedger()


def ledger_from_budget(
    before: Mapping[str, int] | None,
    after: Mapping[str, int],
    *,
    elapsed_ms: float | None = None,
) -> UsageLedger:
    """Per-query delta between two BudgetTracker snapshots (never the cumulative value)."""
    base = before or {}
    values: dict[str, Any] = {
        field: int(after.get(key, 0)) - int(base.get(key, 0))
        for key, field in _BUDGET_FIELDS.items()
        if key in after
    }
    return UsageLedger(elapsed_ms=elapsed_ms, **values)


def ledger_from_cost(cost: Mapping[str, Any], *, elapsed_ms: float | None) -> UsageLedger:
    """Ledger from a per-query ReasoningChain ``cost`` block (embed/rerank unmeasured)."""
    return UsageLedger(
        prompt_tokens=_opt_int(cost.get("prompt_tokens")),
        completion_tokens=_opt_int(cost.get("completion_tokens")),
        llm_calls=_opt_int(cost.get("llm_calls")),
        elapsed_ms=elapsed_ms,
    )


def ledger_from_payload(
    payload: Mapping[str, Any] | None, *, elapsed_ms: float | None
) -> UsageLedger:
    """External adapters report usage as JSON; missing keys stay unknown."""
    data = dict(payload or {})
    fields: dict[str, Any] = {f: _opt_int(data.get(f)) for f in LEDGER_FIELDS if f != "elapsed_ms"}
    return UsageLedger(elapsed_ms=elapsed_ms, **fields)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


class LedgerTotal(BaseModel):
    """Sum over ledgers with per-field coverage (known / n)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    n: int
    totals: dict[str, float]
    known: dict[str, int]

    def coverage(self, field: str) -> float | None:
        return self.known.get(field, 0) / self.n if self.n else None

    def mean(self, field: str) -> float | None:
        k = self.known.get(field, 0)
        return self.totals.get(field, 0.0) / k if k else None


def sum_ledgers(ledgers: Iterable[UsageLedger]) -> LedgerTotal:
    items = list(ledgers)
    totals: dict[str, float] = {}
    known: dict[str, int] = {}
    for field in LEDGER_FIELDS:
        values = [getattr(x, field) for x in items if getattr(x, field) is not None]
        totals[field] = float(sum(values))
        known[field] = len(values)
    return LedgerTotal(n=len(items), totals=totals, known=known)


def money(ledger: UsageLedger, price: PricePoint | None) -> float | None:
    """Generator spend; ``None`` when the price or token counts are unknown."""
    if price is None or ledger.prompt_tokens is None or ledger.completion_tokens is None:
        return None
    return (
        ledger.prompt_tokens * price.prompt_per_million
        + ledger.completion_tokens * price.completion_per_million
    ) / TOKENS_PER_MILLION


def combine(ledgers: Iterable[UsageLedger], *, scope: str = "trial") -> UsageLedger:
    """Sum attempts of one trial; any unknown attempt makes the field unknown."""
    items = list(ledgers)
    values: dict[str, Any] = {}
    for field in LEDGER_FIELDS:
        raw = [getattr(x, field) for x in items]
        values[field] = None if not items or any(v is None for v in raw) else sum(raw)
    return UsageLedger(scope=scope, **values)
