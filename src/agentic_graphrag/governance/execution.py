"""Cooperative query cancellation shared by worker and retrieval threads."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from agentic_graphrag.core.ports.llm import LLMClient
    from agentic_graphrag.governance.budget import BudgetTracker


class ExecutionStopped(BaseException):
    """Cancellation must bypass domain fallback/retry handlers, like CancelledError."""


@dataclass
class ExecutionControl:
    query_id: str = field(default_factory=lambda: str(uuid4()))
    cancelled: threading.Event = field(default_factory=threading.Event)
    deadline: float | None = None
    budget: BudgetTracker | None = None
    llm: LLMClient | None = None
    pending: Future[Any] | None = None
    settled: bool = False

    def check(self) -> None:
        if self.cancelled.is_set():
            raise ExecutionStopped("Query cancelled")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self.cancelled.set()
            raise ExecutionStopped("Query timed out")


current_execution: ContextVar[ExecutionControl | None] = ContextVar("query_execution", default=None)


def check_execution() -> None:
    control = current_execution.get()
    if control is not None:
        control.check()


@contextmanager
def execution_scope(*, reuse: bool = False) -> Iterator[ExecutionControl]:
    control = current_execution.get() if reuse else None
    control = control or ExecutionControl()
    token = current_execution.set(control)
    try:
        yield control
    finally:
        current_execution.reset(token)
