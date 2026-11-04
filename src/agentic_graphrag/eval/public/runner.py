"""Resumable trial runner with retries, abort rule and cost ceiling (B-06, B-08, B-12)."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from agentic_graphrag.eval.public.accounting import money
from agentic_graphrag.eval.public.adapters_base import AdapterResult, BenchAdapter
from agentic_graphrag.eval.public.enums import ArmKind, ArmName, DatasetName, RunKind, TrialStatus
from agentic_graphrag.eval.public.manifest import RunManifest
from agentic_graphrag.eval.public.planning import execution_order
from agentic_graphrag.eval.public.records import QuestionInput
from agentic_graphrag.eval.public.trials import (
    ATTEMPT_END,
    ATTEMPT_INTERRUPTED,
    ATTEMPT_START,
    TRIAL_END,
    TRIAL_START,
    TrialJournal,
    TrialKey,
    TrialState,
)

QuestionLookup = Mapping[tuple[DatasetName, str], QuestionInput]


class AbortRun(RuntimeError):
    """Abort rule or cost ceiling tripped; the journal stays resumable."""


@dataclass
class SpendGuard:
    manifest: RunManifest
    tokens: int = 0
    money_spent: float = 0.0
    unknown_attempts: int = 0
    recent: deque[bool] = field(default_factory=deque)

    def observe(self, result: AdapterResult) -> None:
        rule = self.manifest.ceiling.abort
        self.recent.append(result.error_kind in rule.error_kinds)
        while len(self.recent) > rule.window:
            self.recent.popleft()
        self.add_spend(result)
        if (
            len(self.recent) == rule.window
            and sum(self.recent) / rule.window >= rule.max_error_rate
        ):
            raise AbortRun(
                f"provider error rate >= {rule.max_error_rate:.0%} over {rule.window} attempts"
            )
        self.check_ceiling()

    def add_spend(self, result: AdapterResult) -> None:
        usage = result.usage
        if usage.total_tokens is None:
            self.unknown_attempts += 1
            return
        self.tokens += usage.total_tokens
        cost = money(usage, self.manifest.price_for(self.manifest.models.generator.id))
        self.money_spent += cost or 0.0

    def check_ceiling(self) -> None:
        ceiling = self.manifest.ceiling
        if ceiling.provider_tokens is not None and self.tokens >= ceiling.provider_tokens:
            raise AbortRun(f"provider token ceiling reached ({self.tokens})")
        if ceiling.money is not None and self.money_spent >= ceiling.money:
            raise AbortRun(f"money ceiling reached ({self.money_spent:.2f} {ceiling.currency})")


@dataclass
class RunSummary:
    completed: int = 0
    skipped: int = 0
    aborted: str | None = None


class Runner:
    def __init__(
        self,
        manifest: RunManifest,
        adapters: Mapping[ArmName, BenchAdapter],
        journal: TrialJournal,
        questions: QuestionLookup,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.manifest = manifest
        self.adapters = adapters
        self.journal = journal
        self.questions = questions
        self.sleep = sleep
        self.states = journal.states()
        self.guard = SpendGuard(manifest)
        self._lock = threading.Lock()
        for state in self.states.values():
            for attempt in state.attempt_starts:
                self.guard.add_spend(state.attempt_result(attempt))

    def run(self, keys: Sequence[TrialKey]) -> RunSummary:
        summary = RunSummary()
        try:
            self.guard.check_ceiling()
            for block in execution_order(self.manifest, keys):
                self._run_block(list(block.keys), summary)
        except AbortRun as exc:
            summary.aborted = str(exc)
        return summary

    def _run_block(self, keys: list[TrialKey], summary: RunSummary) -> None:
        workers = self.manifest.execution.workers
        if workers == 1:
            outcomes = [self.run_trial(k) for k in keys]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                outcomes = list(pool.map(self.run_trial, keys))
        summary.completed += sum(outcomes)
        summary.skipped += len(outcomes) - sum(outcomes)

    def _record(self, event: str, key: TrialKey, **fields: object) -> None:
        with self._lock:
            self.journal.append(event, key, **fields)

    def run_trial(self, key: TrialKey) -> bool:
        """Complete one trial; ``False`` when it was already finished (resume)."""
        state = self.states.setdefault(key.id, TrialState(key=key))
        if state.done:
            return False
        if not state.started:
            self._record(TRIAL_START, key)
            state.started = True
        for attempt in state.dangling():
            self._record(ATTEMPT_INTERRUPTED, key, attempt=attempt)
            state.interrupted.add(attempt)
        n = max(state.attempt_starts, default=0)
        last = state.attempt_result(n) if n else None
        while self._should_attempt(last, n):
            if last is not None:
                self.sleep(self.manifest.retry.backoff(n))
            n += 1
            last = self._attempt(state, n)
        assert last is not None
        self._record(TRIAL_END, key, terminal_attempt=n, status=last.status.value)
        state.terminal_attempt = n
        return True

    def _attempt(self, state: TrialState, attempt: int) -> AdapterResult:
        key = state.key
        self._record(ATTEMPT_START, key, attempt=attempt)
        state.attempt_starts.append(attempt)
        question = self.questions[(key.dataset, key.question_id)]
        result = self.adapters[key.arm].answer(question, run_seed=key.run_seed)
        self._record(ATTEMPT_END, key, attempt=attempt, result=result.model_dump(mode="json"))
        state.results[attempt] = result
        with self._lock:
            self.guard.observe(result)
        return result

    def _should_attempt(self, last: AdapterResult | None, attempts: int) -> bool:
        if attempts >= self.manifest.retry.max_attempts:
            return False
        if last is None or last.status is TrialStatus.INTERRUPTED:
            return True
        return last.failed and last.error_kind in self.manifest.retry.retry_on


def preflight_blockers(manifest: RunManifest, *, has_credentials: bool) -> list[str]:
    """Reasons a live run must not start; each one keeps the run 'Not Executed'."""
    blockers: list[str] = []
    if manifest.run_kind is RunKind.FIXTURE:
        return blockers
    if not has_credentials:
        blockers.append("no live LLM credentials (LLM_API_KEY) configured")
    if manifest.run_kind in {RunKind.PILOT, RunKind.FINAL}:
        if manifest.ceiling.money is None or manifest.ceiling.provider_tokens is None:
            blockers.append("B-12 cost ceiling (money and provider tokens) not recorded")
        if not manifest.prices:
            blockers.append("no dated model prices in the manifest")
    if manifest.run_kind is RunKind.FINAL:
        if not manifest.protocol.verified:
            blockers.append("upstream protocol revision UNVERIFIED")
        if not manifest.promotion.declared:
            blockers.append("B-10 promotion bounds not predeclared")
    for arm in manifest.arms:
        if arm.kind is ArmKind.EXTERNAL and arm.external and not arm.external.package_revision:
            blockers.append(f"{arm.name}: external package revision UNVERIFIED")
    return blockers
