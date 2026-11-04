"""Durable, append-only trial journal (B-06).

Every trial start and every attempt is journaled *before* dispatch. On resume, an
attempt that started but never ended is recorded as interrupted with unknown spend —
it consumed an attempt and is never a free retry. The terminal result of a trial is
always its last attempt (never the best one).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from agentic_graphrag.eval.public.accounting import UNKNOWN_USAGE, UsageLedger, combine
from agentic_graphrag.eval.public.adapters_base import AdapterResult
from agentic_graphrag.eval.public.enums import ArmName, DatasetName, ErrorKind, TrialStatus

TRIAL_START = "trial_start"
ATTEMPT_START = "attempt_start"
ATTEMPT_END = "attempt_end"
ATTEMPT_INTERRUPTED = "attempt_interrupted"
TRIAL_END = "trial_end"


class TrialKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    manifest_hash: str
    arm: ArmName
    dataset: DatasetName
    run_seed: int
    question_id: str

    @property
    def id(self) -> str:
        return "|".join(
            (
                self.manifest_hash,
                self.arm.value,
                self.dataset.value,
                str(self.run_seed),
                self.question_id,
            )
        )


INTERRUPTED_RESULT = AdapterResult(
    status=TrialStatus.INTERRUPTED,
    error="attempt dispatched but never completed (crash/kill); spend unknown",
    error_kind=ErrorKind.INTERRUPTED,
    usage=UNKNOWN_USAGE,
)


@dataclass
class TrialState:
    key: TrialKey
    started: bool = False
    attempt_starts: list[int] = field(default_factory=list)
    results: dict[int, AdapterResult] = field(default_factory=dict)
    interrupted: set[int] = field(default_factory=set)
    terminal_attempt: int | None = None
    end_events: int = 0

    @property
    def done(self) -> bool:
        return self.terminal_attempt is not None

    def dangling(self) -> list[int]:
        """Attempts journaled as started with neither an end nor an interruption."""
        return [
            a for a in self.attempt_starts if a not in self.results and a not in self.interrupted
        ]

    def attempt_result(self, attempt: int) -> AdapterResult:
        return self.results.get(attempt, INTERRUPTED_RESULT)

    @property
    def terminal(self) -> AdapterResult | None:
        if self.terminal_attempt is None:
            return None
        return self.attempt_result(self.terminal_attempt)

    def usage(self) -> UsageLedger:
        """All attempts' spend; one interrupted attempt makes the total unknown."""
        return combine([self.attempt_result(a).usage for a in self.attempt_starts])

    def known_usage(self) -> UsageLedger:
        """Lower bound: completed attempts only."""
        return combine([self.results[a].usage for a in self.attempt_starts if a in self.results])


class TrialJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, key: TrialKey, **fields: Any) -> None:
        row = {"event": event, "key": key.model_dump(mode="json"), "ts": time.time(), **fields}
        line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def states(self) -> dict[str, TrialState]:
        out: dict[str, TrialState] = {}
        for row in self.events():
            key = TrialKey.model_validate(row["key"])
            state = out.setdefault(key.id, TrialState(key=key))
            _apply(state, row)
        return out


def _apply(state: TrialState, row: dict[str, Any]) -> None:
    event = row["event"]
    if event == TRIAL_START:
        state.started = True
    elif event == ATTEMPT_START:
        state.attempt_starts.append(int(row["attempt"]))
    elif event == ATTEMPT_END:
        state.results[int(row["attempt"])] = AdapterResult.model_validate(row["result"])
    elif event == ATTEMPT_INTERRUPTED:
        state.interrupted.add(int(row["attempt"]))
    elif event == TRIAL_END:
        state.end_events += 1
        state.terminal_attempt = int(row["terminal_attempt"])
    else:
        raise ValueError(f"unknown journal event {event!r}")
