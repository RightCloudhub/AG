"""Loop guardrails: hops, LLM calls, tokens, timeout, recursion (FR-AG-06/07, P2-AG-04).

All limits load from ``configs/default.yaml`` → ``AppConfig.guardrails`` via
``GuardrailConfig.from_app_config``. Request-level overrides (e.g. API
``max_hops``) are applied through ``with_overrides`` without mutating global config.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from agentic_graphrag.llm.budget import BudgetExceeded, BudgetTracker

if TYPE_CHECKING:
    from agentic_graphrag.config import AppConfig, GuardrailsConfig


def min_recursion_limit(max_hops: int) -> int:
    """Minimum LangGraph ``recursion_limit`` for a given hop budget.

    Worst-case node visits: planner + (max_hops+1)×(executor+critic) + answer.
    LangGraph raises when the limit is reached without END, so the limit must be
    strictly greater than that visit count → ``2 * max_hops + 5``.
    """
    hops = max(1, int(max_hops))
    return 2 * hops + 5


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
    # BL-12: sub-question count budget (separate from graph-traversal hop
    # budget). When 0, falls back to ``max_hops`` for backward compatibility.
    max_sub_questions: int = 0
    hard_max_sub_questions: int = 50

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
        max_sub_questions: int | None = None,
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
        rec = recursion_limit if recursion_limit is not None else int(g.recursion_limit)
        # Never ship a recursion_limit that the hop budget can exhaust.
        rec = max(int(rec), min_recursion_limit(hops))
        # BL-12: resolve sub-question budget; 0 in YAML means "use max_hops".
        sq_budget = max_sub_questions if max_sub_questions is not None else int(
            getattr(g, "max_sub_questions", 0) or 0
        )
        if sq_budget <= 0:
            sq_budget = hops

        return cls(
            max_hops=hops,
            max_llm_calls=(max_llm_calls if max_llm_calls is not None else int(g.max_llm_calls)),
            max_tokens=(max_tokens if max_tokens is not None else int(g.max_tokens_per_query)),
            query_timeout_seconds=(
                query_timeout_seconds
                if query_timeout_seconds is not None
                else int(g.query_timeout_seconds)
            ),
            recursion_limit=rec,
            hard_max_hops=hard,
            max_sub_questions=min(int(sq_budget), 50),
            hard_max_sub_questions=50,
        )

    def with_overrides(
        self,
        *,
        max_hops: int | None = None,
        max_llm_calls: int | None = None,
        max_tokens: int | None = None,
        query_timeout_seconds: int | None = None,
        recursion_limit: int | None = None,
        max_sub_questions: int | None = None,
    ) -> GuardrailConfig:
        hops = self.max_hops if max_hops is None else max(1, min(int(max_hops), self.hard_max_hops))
        rec = self.recursion_limit if recursion_limit is None else int(recursion_limit)
        rec = max(rec, min_recursion_limit(hops))
        sq = self.max_sub_questions if max_sub_questions is None else max(
            1, min(int(max_sub_questions), self.hard_max_sub_questions)
        )
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
            recursion_limit=rec,
            max_sub_questions=sq,
        )

    def budget_tracker(self) -> BudgetTracker:
        return BudgetTracker(max_llm_calls=self.max_llm_calls, max_tokens=self.max_tokens)

    @property
    def sub_question_cap(self) -> int:
        """BL-12: effective sub-question iteration cap.

        Returns ``max_sub_questions`` when set (>0); otherwise falls back to
        ``max_hops`` for backward compatibility with pre-BL-12 configs.
        """
        return self.max_sub_questions or self.max_hops


@dataclass
class GuardrailState:
    hop: int = 0
    tripped: bool = False
    reason: str = ""
    started_at: float = 0.0


class Guardrails:
    def __init__(self, config: GuardrailConfig, budget: BudgetTracker | None = None) -> None:
        self.config = config
        self.budget = budget or config.budget_tracker()
        self.state = GuardrailState(started_at=time.monotonic())

    def on_hop_start(self) -> None:
        self.state.hop += 1
        self._check()

    def _check(self) -> None:
        # BL-12: ``hop`` counts sub-question iterations; cap it with
        # ``max_sub_questions`` (separate from ``max_hops`` which now only
        # governs graph-traversal depth). When ``max_sub_questions`` is 0
        # (legacy), fall back to ``max_hops`` for backward compatibility.
        sq_cap = self.config.sub_question_cap
        if self.state.hop > sq_cap:
            self.state.tripped = True
            # Keep "max_hops exceeded" text when falling back to max_hops
            # (backward compat with tests/monitoring that match on it).
            label = (
                "max_sub_questions" if self.config.max_sub_questions > 0 else "max_hops"
            )
            self.state.reason = f"{label} exceeded ({self.state.hop}/{sq_cap})"
            return
        # Wall-clock timeout (P3 / F9 fix)
        if self.config.query_timeout_seconds > 0 and self.state.started_at > 0:
            elapsed = time.monotonic() - self.state.started_at
            if elapsed > self.config.query_timeout_seconds:
                self.state.tripped = True
                self.state.reason = (
                    f"query_timeout exceeded ({elapsed:.1f}s/{self.config.query_timeout_seconds}s)"
                )
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
            f"ok sub_questions={self.state.hop}/{self.config.sub_question_cap} "
            f"max_hops={self.config.max_hops} "
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
