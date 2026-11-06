"""Per-query LLM cost / call budget (FR-AG-06, FR-OP-02)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field

from agentic_graphrag.core.enums import LLMRole
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.execution import check_execution

LLM_BY_ROLE_KEY = "llm_by_role"
DEGRADED_FROM_KEY = "degraded_from"
DEGRADED_FROM_STRONG = "strong"

_current_role: ContextVar[LLMRole] = ContextVar("agr_llm_role", default=LLMRole.UNKNOWN)


@contextmanager
def role_scope(role: LLMRole) -> Iterator[None]:
    """Attribute the calls made inside this block to ``role``."""
    token = _current_role.set(role)
    try:
        yield
    finally:
        _current_role.reset(token)


class BudgetExceeded(Exception):
    """Raised when a hard budget guardrail is hit."""

    def __init__(self, reason: str, tracker: BudgetTracker) -> None:
        super().__init__(reason)
        self.reason = reason
        self.tracker = tracker


@dataclass
class RoleUsage:
    """What one LLM role spent in this query. ``llm_ms`` is remote time only."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_ms: int = 0
    failures: int = 0
    parse_failures: int = 0


@dataclass
class BudgetTracker:
    """The single meter of one query, shared by every role and worker thread."""

    max_llm_calls: int = 20
    max_tokens: int = 50_000
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Embedding / rerank calls: metered (cost, observability) but not counted
    # against the chat-call cap that bounds reasoning hops (refactor §5.3).
    embed_calls: int = 0
    tier_fallbacks: int = 0
    events: list[str] = field(default_factory=list)
    by_role: dict[LLMRole, RoleUsage] = field(default_factory=dict)
    parent: BudgetTracker | None = field(default=None, repr=False, compare=False, kw_only=True)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def check(self) -> None:
        with self._lock:
            self._check_limits()
            if self.parent is not None:
                self.parent.check()

    def _check_limits(self) -> None:
        check_execution()
        if self.llm_calls >= self.max_llm_calls:
            raise BudgetExceeded(
                f"max_llm_calls reached ({self.llm_calls}/{self.max_llm_calls})",
                self,
            )
        self._check_tokens()

    def check_tokens(self) -> None:
        """Check an embedding's token budget without requiring a chat-call slot."""
        with self._lock:
            self._check_tokens()
            if self.parent is not None:
                self.parent.check_tokens()

    def _check_tokens(self) -> None:
        if self.total_tokens >= self.max_tokens:
            raise BudgetExceeded(
                f"max_tokens reached ({self.total_tokens}/{self.max_tokens})",
                self,
            )

    def _usage(self, role: LLMRole | None = None) -> RoleUsage:
        return self.by_role.setdefault(role or _current_role.get(), RoleUsage())

    def reserve_call(self) -> None:
        """Claim a chat-call slot before paying for it: concurrent callers cannot overshoot."""
        with self._lock:
            self._check_limits()
            if self.parent is not None:
                self.parent.reserve_call()
            self.llm_calls += 1

    def release_call(self) -> None:
        """Return a reserved slot whose call produced nothing to pay for."""
        with self._lock:
            if self.parent is not None:
                self.parent.release_call()
            self.llm_calls = max(0, self.llm_calls - 1)

    def settle_call(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        """Book a reserved call. It is already paid for, so this never raises."""
        with self._lock:
            if self.parent is not None:
                self.parent.settle_call(prompt_tokens, completion_tokens)
            self._book_tokens(prompt_tokens, completion_tokens)

    def record_call(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        """Reserve and settle one call that has no remote step (replay, stub)."""
        with self._lock:
            self._check_limits()
            if self.parent is not None:
                self.parent.record_call(prompt_tokens, completion_tokens)
            self.llm_calls += 1
            self._book_tokens(prompt_tokens, completion_tokens)

    def _book_tokens(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens += max(0, prompt_tokens)
        self.completion_tokens += max(0, completion_tokens)
        usage = self._usage()
        usage.calls += 1
        usage.prompt_tokens += max(0, prompt_tokens)
        usage.completion_tokens += max(0, completion_tokens)
        self.events.append(
            f"call#{self.llm_calls} prompt={prompt_tokens} completion={completion_tokens}"
        )

    def record_embedding(self, tokens: int = 0) -> None:
        """Meter one finished embedding call: its tokens count, it is not a chat call."""
        with self._lock:
            if self.parent is not None:
                self.parent.record_embedding(tokens)
            self.embed_calls += 1
            self.prompt_tokens += max(0, tokens)
            usage = self._usage(LLMRole.EMBEDDING)
            usage.calls += 1
            usage.prompt_tokens += max(0, tokens)
            self.events.append(f"embed#{self.embed_calls} tokens={tokens}")

    def record_remote(self, elapsed_ms: int, *, failed: bool, role: LLMRole | None = None) -> None:
        """Time spent waiting on the gateway, whether or not the call succeeded."""
        with self._lock:
            if self.parent is not None:
                self.parent.record_remote(elapsed_ms, failed=failed, role=role)
            usage = self._usage(role)
            usage.llm_ms += max(0, elapsed_ms)
            usage.failures += int(failed)

    def note_parse_failure(self) -> None:
        with self._lock:
            if self.parent is not None:
                self.parent.note_parse_failure()
            self._usage().parse_failures += 1

    def note_tier_fallback(self) -> None:
        with self._lock:
            if self.parent is not None:
                self.parent.note_tier_fallback()
            self.tier_fallbacks += 1

    def restore_usage(self, snapshot: dict[str, int]) -> None:
        """Restore checkpoint totals without lowering already metered usage.

        Only use at graph boundaries, never to aggregate concurrent scopes:
        scopes account to their parent when each call completes. Old checkpoints
        can restore totals, but cannot reconstruct unpersisted failed calls.
        """
        with self._lock:
            for key in ("llm_calls", "embed_calls", "prompt_tokens", "completion_tokens"):
                setattr(self, key, max(getattr(self, key), snapshot.get(key, 0)))

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "embed_calls": self.embed_calls,
                "llm_calls": self.llm_calls,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
            }

    def role_usage(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {str(role): asdict(usage) for role, usage in sorted(self.by_role.items())}


def apply_budget(chain: ReasoningChain, budget: BudgetTracker | None) -> None:
    """Copy the meter onto the chain; runs that used no LLM gain no metadata."""
    if budget is None:
        return
    snap = budget.snapshot()
    chain.cost.llm_calls = snap["llm_calls"]
    chain.cost.embed_calls = snap["embed_calls"]
    chain.cost.tokens = snap["total_tokens"]
    chain.cost.prompt_tokens = snap["prompt_tokens"]
    chain.cost.completion_tokens = snap["completion_tokens"]
    extra: dict[str, object] = {}
    usage = budget.role_usage()
    if usage:
        extra[LLM_BY_ROLE_KEY] = usage
    if budget.tier_fallbacks:
        extra[DEGRADED_FROM_KEY] = DEGRADED_FROM_STRONG
    if extra:
        chain.metadata = {**(chain.metadata or {}), **extra}
