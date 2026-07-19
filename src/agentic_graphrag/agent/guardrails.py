"""Loop guardrails: hops, LLM calls, tokens, timeout, recursion (FR-AG-06/07, P2-AG-04).

All limits load from ``configs/default.yaml`` → ``AppConfig.guardrails`` via
``GuardrailConfig.from_app_config``. Request-level overrides (e.g. API
``max_hops``) are applied through ``with_overrides`` without mutating global config.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from agentic_graphrag.llm.budget import BudgetExceeded, BudgetTracker

if TYPE_CHECKING:
    from agentic_graphrag.config import AppConfig, GuardrailsConfig


@dataclass(frozen=True)
class GuardrailConfig:
    """Runtime guardrail limits (immutable snapshot for one query)."""

    max_hops: int = 5
    max_llm_calls: int = 20
    max_tokens: int = 50_000
    query_timeout_seconds: int = 60
    recursion_limit: int = 15
    # Server hard ceiling — request overrides cannot exceed this
    hard_max_hops: int = 20

    @classmethod
    def from_app_config(
        cls,
        cfg: AppConfig | GuardrailsConfig | None = None,
        *,
        max_hops: int | None = None,
        max_llm_calls: int | None = None,
        max_tokens: int | None = None,
        query_timeout_seconds: int | None = None,
        recursion_limit: int | None = None,
    ) -> GuardrailConfig:
        """Build from YAML/app config with optional per-request overrides (P2-AG-04)."""
        if cfg is None:
            from agentic_graphrag.config import get_config

            g = get_config().guardrails
        elif hasattr(cfg, "guardrails"):
            g = cfg.guardrails  # type: ignore[union-attr]
        else:
            g = cfg  # GuardrailsConfig

        hard = 20
        hops = max_hops if max_hops is not None else g.max_hops
        hops = max(1, min(int(hops), hard))

        return cls(
            max_hops=hops,
            max_llm_calls=(max_llm_calls if max_llm_calls is not None else int(g.max_llm_calls)),
            max_tokens=(max_tokens if max_tokens is not None else int(g.max_tokens_per_query)),
            query_timeout_seconds=(
                query_timeout_seconds
                if query_timeout_seconds is not None
                else int(g.query_timeout_seconds)
            ),
            recursion_limit=(
                recursion_limit if recursion_limit is not None else int(g.recursion_limit)
            ),
            hard_max_hops=hard,
        )

    def with_overrides(
        self,
        *,
        max_hops: int | None = None,
        max_llm_calls: int | None = None,
        max_tokens: int | None = None,
        query_timeout_seconds: int | None = None,
        recursion_limit: int | None = None,
    ) -> GuardrailConfig:
        hops = self.max_hops if max_hops is None else max(1, min(int(max_hops), self.hard_max_hops))
        return replace(
            self,
            max_hops=hops,
            max_llm_calls=self.max_llm_calls if max_llm_calls is None else int(max_llm_calls),
            max_tokens=self.max_tokens if max_tokens is None else int(max_tokens),
            query_timeout_seconds=(
                self.query_timeout_seconds
                if query_timeout_seconds is None
                else int(query_timeout_seconds)
            ),
            recursion_limit=(
                self.recursion_limit if recursion_limit is None else int(recursion_limit)
            ),
        )

    def budget_tracker(self) -> BudgetTracker:
        return BudgetTracker(max_llm_calls=self.max_llm_calls, max_tokens=self.max_tokens)


@dataclass
class GuardrailState:
    hop: int = 0
    tripped: bool = False
    reason: str = ""


class Guardrails:
    def __init__(self, config: GuardrailConfig, budget: BudgetTracker | None = None) -> None:
        self.config = config
        self.budget = budget or config.budget_tracker()
        self.state = GuardrailState()

    def on_hop_start(self) -> None:
        self.state.hop += 1
        self._check()

    def _check(self) -> None:
        if self.state.hop > self.config.max_hops:
            self.state.tripped = True
            self.state.reason = f"max_hops exceeded ({self.state.hop}/{self.config.max_hops})"
            return
        try:
            self.budget.check()
        except BudgetExceeded as exc:
            self.state.tripped = True
            self.state.reason = str(exc.reason)

    def assert_ok(self) -> None:
        self._check()
        if self.state.tripped:
            raise BudgetExceeded(self.state.reason or "guardrail tripped", self.budget)

    def status_text(self) -> str:
        if self.state.tripped:
            return f"tripped: {self.state.reason}"
        return (
            f"ok hop={self.state.hop}/{self.config.max_hops} "
            f"llm_calls={self.budget.llm_calls}/{self.config.max_llm_calls} "
            f"tokens={self.budget.total_tokens}/{self.config.max_tokens} "
            f"timeout_s={self.config.query_timeout_seconds} "
            f"recursion_limit={self.config.recursion_limit}"
        )

    def fallback_summary(self, explored_paths: list[str] | None = None) -> str:
        """Message used when guardrails trip (FR-AG-06/07 partial/no-answer path)."""
        paths = explored_paths or []
        path_txt = "; ".join(paths[:20]) if paths else "(none)"
        return (
            f"Guardrail stop: {self.state.reason or self.status_text()}. "
            f"Explored paths: {path_txt}."
        )
