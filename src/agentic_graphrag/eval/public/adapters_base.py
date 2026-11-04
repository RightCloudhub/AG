"""Typed adapter boundary shared by all six arms (B-05)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.eval.public.accounting import UNKNOWN_USAGE, UsageLedger
from agentic_graphrag.eval.public.alignment import AlignmentReport
from agentic_graphrag.eval.public.enums import ArmName, ErrorKind, TrialStatus
from agentic_graphrag.eval.public.records import Passage, QuestionInput

_RATE_LIMIT_MARKERS = ("429", "rate limit", "ratelimit", "too many requests")
_FORBIDDEN_MARKERS = ("403", "forbidden")
_TIMEOUT_MARKERS = ("timeout", "timed out", "504")
_TRANSPORT_MARKERS = ("connect", "connection", "transport", "503", "unavailable", "circuit")


class AdapterResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    prediction: str = ""
    status: TrialStatus
    native_status: str | None = None
    confidence: float | None = None
    retrieved_passage_ids: tuple[str, ...] | None = None
    context_passage_ids: tuple[str, ...] | None = None
    cited_passage_ids: tuple[str, ...] | None = None
    alignment: dict[str, AlignmentReport] = Field(default_factory=dict)
    usage: UsageLedger = UNKNOWN_USAGE
    timings_ms: dict[str, float] = Field(default_factory=dict)
    seed_control: str = "not_supported"
    error: str | None = None
    error_kind: ErrorKind | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status in {TrialStatus.ERROR, TrialStatus.TIMEOUT}


class PrepareReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    arm: ArmName
    passages: int
    corpus_sha256: str
    usage: UsageLedger = Field(default_factory=lambda: UsageLedger(scope="indexing"))
    stats: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class BenchAdapter(Protocol):
    """``prepare`` indexes permitted corpus text only; ``answer`` sees no gold labels."""

    arm: ArmName

    def prepare(self, passages: list[Passage], corpus_sha256: str) -> PrepareReport: ...

    def answer(self, question: QuestionInput, *, run_seed: int) -> AdapterResult: ...

    def close(self) -> None: ...


def classify_error(exc: BaseException) -> ErrorKind:
    """Map an exception to a retry-policy error kind (status codes and transport names)."""
    status = getattr(exc, "status_code", None)
    text = f"{type(exc).__name__} {exc} {status or ''}".lower()
    for kind, markers in (
        (ErrorKind.RATE_LIMIT, _RATE_LIMIT_MARKERS),
        (ErrorKind.FORBIDDEN, _FORBIDDEN_MARKERS),
        (ErrorKind.TIMEOUT, _TIMEOUT_MARKERS),
        (ErrorKind.TRANSPORT, _TRANSPORT_MARKERS),
    ):
        if any(m in text for m in markers):
            return kind
    if "budget" in text:
        return ErrorKind.BUDGET
    return ErrorKind.OTHER


def error_result(
    exc: BaseException, *, elapsed_ms: float, usage: UsageLedger | None = None
) -> AdapterResult:
    """Failed attempt: measured elapsed time and known-or-unknown usage, never zeros."""
    kind = classify_error(exc)
    status = TrialStatus.TIMEOUT if kind is ErrorKind.TIMEOUT else TrialStatus.ERROR
    ledger = (usage or UNKNOWN_USAGE).model_copy(update={"elapsed_ms": elapsed_ms})
    return AdapterResult(
        status=status,
        error=f"{type(exc).__name__}: {exc}"[:500],
        error_kind=kind,
        usage=ledger,
        timings_ms={"total": elapsed_ms},
    )
