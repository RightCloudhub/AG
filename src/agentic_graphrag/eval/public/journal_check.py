"""Journal validation before any comparison (B-06): duplicates, missing, manifest agreement."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from agentic_graphrag.eval.public.manifest import RunManifest, manifest_hash
from agentic_graphrag.eval.public.trials import TrialJournal, TrialKey, TrialState

MAX_LISTED = 50


class JournalValidation(BaseModel):
    expected: int
    completed: int
    errors: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    interrupted_attempts: int = 0
    unknown_spend_trials: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors and not self.missing

    def summary(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "expected": self.expected,
            "completed": self.completed,
            "missing": len(self.missing),
            "errors": self.errors[:MAX_LISTED],
            "interrupted_attempts": self.interrupted_attempts,
            "unknown_spend_trials": self.unknown_spend_trials,
        }


def _state_errors(state: TrialState) -> list[str]:
    errors: list[str] = []
    kid = state.key.id
    if state.end_events > 1:
        errors.append(f"{kid}: duplicate trial_end ({state.end_events})")
    if len(state.attempt_starts) != len(set(state.attempt_starts)):
        errors.append(f"{kid}: duplicate attempt numbers")
    if sorted(state.attempt_starts) != list(range(1, len(state.attempt_starts) + 1)):
        errors.append(f"{kid}: attempt numbers are not 1..n")
    orphan = set(state.results) - set(state.attempt_starts)
    if orphan:
        errors.append(f"{kid}: attempt_end without attempt_start {sorted(orphan)}")
    if state.done and state.terminal_attempt != max(state.attempt_starts, default=0):
        errors.append(f"{kid}: terminal result is not the last attempt")
    if state.done and state.dangling():
        errors.append(f"{kid}: finished with an unresolved attempt")
    return errors


def validate_journal(
    journal: TrialJournal, manifest: RunManifest, expected: Sequence[TrialKey]
) -> JournalValidation:
    digest = manifest_hash(manifest)
    states = journal.states()
    errors: list[str] = []
    for state in states.values():
        if state.key.manifest_hash != digest:
            errors.append(f"{state.key.id}: recorded under another manifest hash")
        errors.extend(_state_errors(state))
    expected_ids = {k.id for k in expected}
    done = {kid for kid, s in states.items() if s.done}
    unexpected = sorted(done - expected_ids)
    errors.extend(f"{kid}: not part of the planned trials" for kid in unexpected[:MAX_LISTED])
    interrupted = sum(len(s.interrupted) + len(s.dangling()) for s in states.values())
    unknown = sum(1 for s in states.values() if s.done and s.usage().total_tokens is None)
    return JournalValidation(
        expected=len(expected_ids),
        completed=len(done & expected_ids),
        errors=errors,
        missing=sorted(expected_ids - done),
        interrupted_attempts=interrupted,
        unknown_spend_trials=unknown,
    )
