"""Shared types for extract pipeline (P2-KG-01)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from agentic_graphrag.knowledge.schema_check import ExtractResult, SchemaDefinition, Triple
from agentic_graphrag.stores.interfaces import ChunkRecord


class ExtractStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"
    EMPTY = "empty"


@dataclass
class RetryPolicy:
    """Retry policy written as logic (P2-KG-01) — no live calls here."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.0  # 0 in unit tests; set >0 for production backoff
    retryable_exceptions: tuple[type[BaseException], ...] = (Exception,)

    def should_retry(self, attempt: int, exc: BaseException | None) -> bool:
        """``attempt`` is 1-based count of tries already performed."""
        if attempt >= self.max_attempts:
            return False
        if exc is None:
            return False
        return isinstance(exc, self.retryable_exceptions)

    def delay_before_attempt(self, attempt: int) -> float:
        """Backoff before attempt N (1-based). First attempt → 0."""
        if attempt <= 1 or self.base_delay_seconds <= 0:
            return 0.0
        return self.base_delay_seconds * (2 ** (attempt - 2))


@dataclass
class ChunkExtractResult:
    chunk_id: str
    doc_id: str
    status: ExtractStatus
    accepted: list[Triple] = field(default_factory=list)
    rejected: list[tuple[Triple, str]] = field(default_factory=list)
    attempts: int = 0
    error: str | None = None
    elapsed_ms: int = 0

    def journal_row(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "status": self.status.value,
            "attempts": self.attempts,
            "n_accepted": len(self.accepted),
            "n_rejected": len(self.rejected),
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
            "ts": datetime.now(UTC).isoformat(),
        }


@dataclass
class PipelineResult:
    accepted: list[Triple]
    rejected: list[tuple[Triple, str]]
    chunk_results: list[ChunkExtractResult]
    completed_chunk_ids: set[str]
    failed_chunk_ids: set[str]

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.chunk_results if r.status == ExtractStatus.OK)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.chunk_results if r.status == ExtractStatus.FAILED)

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.chunk_results if r.status == ExtractStatus.SKIPPED)


ExtractFn = Callable[[ChunkRecord, SchemaDefinition], ExtractResult]
