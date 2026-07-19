"""LLM triple extraction pipeline (FR-KG-01/02 / P2-KG-01).

Engineering features:
- per-chunk tasks with JSONL journal (resume by completed ``chunk_id``)
- pure-logic retry policy (no live HTTP in the policy itself)
- failure quarantine JSONL
- provenance metadata on accepted triples + optional DocStore update
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from agentic_graphrag.config import load_prompt
from agentic_graphrag.knowledge.schema_check import (
    ExtractResult,
    SchemaDefinition,
    Triple,
    gate_triples,
)
from agentic_graphrag.llm.provider import LLMProvider, Message, Tier
from agentic_graphrag.llm.structured import complete_structured
from agentic_graphrag.stores.interfaces import ChunkRecord, DocStore


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


def extract_from_chunk(
    chunk: ChunkRecord,
    schema: SchemaDefinition,
    llm: LLMProvider,
    *,
    confidence_threshold: float = 0.5,
) -> tuple[list[Triple], list[tuple[Triple, str]]]:
    """Single-chunk LLM extract + gate (backward-compatible)."""
    result = _llm_extract_raw(chunk, schema, llm)
    for t in result.triples:
        t.source_doc_id = t.source_doc_id or chunk.doc_id
        t.source_chunk_id = t.source_chunk_id or chunk.chunk_id
        _stamp_provenance(t, chunk)
    gated = gate_triples(result.triples, schema, confidence_threshold=confidence_threshold)
    return gated.accepted, gated.rejected


def extract_from_chunks(
    chunks: list[ChunkRecord],
    schema: SchemaDefinition,
    llm: LLMProvider,
    *,
    confidence_threshold: float = 0.5,
) -> tuple[list[Triple], list[tuple[Triple, str]]]:
    """Simple sequential extract (no journal). Prefer :func:`run_extract_pipeline`."""
    all_accepted: list[Triple] = []
    all_rejected: list[tuple[Triple, str]] = []
    for chunk in chunks:
        accepted, rejected = extract_from_chunk(
            chunk, schema, llm, confidence_threshold=confidence_threshold
        )
        all_accepted.extend(accepted)
        all_rejected.extend(rejected)
    return all_accepted, all_rejected


def load_completed_chunk_ids(journal_path: str | Path) -> set[str]:
    """Resume set: chunk_ids with status ok/empty in journal."""
    path = Path(journal_path)
    done: set[str] = set()
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("status") in {ExtractStatus.OK.value, ExtractStatus.EMPTY.value}:
            cid = row.get("chunk_id")
            if cid:
                done.add(str(cid))
    return done


def run_extract_pipeline(
    chunks: list[ChunkRecord],
    schema: SchemaDefinition,
    *,
    llm: LLMProvider | None = None,
    extract_fn: ExtractFn | None = None,
    confidence_threshold: float = 0.5,
    retry: RetryPolicy | None = None,
    journal_path: str | Path | None = None,
    quarantine_path: str | Path | None = None,
    doc_store: DocStore | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    batch_id: str | None = None,
) -> PipelineResult:
    """Taskized per-chunk extraction with journal, retry, quarantine, provenance.

    ``extract_fn`` is injectable for offline unit tests (no live LLM).
    When omitted, uses ``llm`` via structured completion.
    """
    if extract_fn is None and llm is None:
        raise ValueError("run_extract_pipeline requires llm or extract_fn")
    retry = retry or RetryPolicy()
    sleep = sleep_fn or time.sleep
    batch = batch_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    completed = load_completed_chunk_ids(journal_path) if journal_path else set()

    journal_f = None
    quarantine_f = None
    if journal_path:
        jp = Path(journal_path)
        jp.parent.mkdir(parents=True, exist_ok=True)
        journal_f = jp.open("a", encoding="utf-8")
    if quarantine_path:
        qp = Path(quarantine_path)
        qp.parent.mkdir(parents=True, exist_ok=True)
        quarantine_f = qp.open("a", encoding="utf-8")

    all_accepted: list[Triple] = []
    all_rejected: list[tuple[Triple, str]] = []
    chunk_results: list[ChunkExtractResult] = []
    failed_ids: set[str] = set()
    # provenance aggregation: doc_id → list of chunk outcomes
    doc_provenance: dict[str, list[dict[str, Any]]] = {}

    try:
        for chunk in chunks:
            if chunk.chunk_id in completed:
                cr = ChunkExtractResult(
                    chunk_id=chunk.chunk_id,
                    doc_id=chunk.doc_id,
                    status=ExtractStatus.SKIPPED,
                    attempts=0,
                )
                chunk_results.append(cr)
                continue

            cr = _extract_chunk_with_retry(
                chunk,
                schema,
                extract_fn=extract_fn,
                llm=llm,
                confidence_threshold=confidence_threshold,
                retry=retry,
                sleep=sleep,
                batch_id=batch,
            )
            chunk_results.append(cr)
            if journal_f is not None:
                journal_f.write(json.dumps(cr.journal_row(), ensure_ascii=False) + "\n")
                journal_f.flush()

            doc_provenance.setdefault(chunk.doc_id, []).append(cr.journal_row())

            if cr.status == ExtractStatus.FAILED:
                failed_ids.add(chunk.chunk_id)
                if quarantine_f is not None:
                    quarantine_f.write(
                        json.dumps(
                            {
                                "chunk_id": chunk.chunk_id,
                                "doc_id": chunk.doc_id,
                                "error": cr.error,
                                "attempts": cr.attempts,
                                "batch_id": batch,
                                "text_preview": chunk.text[:300],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    quarantine_f.flush()
                continue

            all_accepted.extend(cr.accepted)
            all_rejected.extend(cr.rejected)
            if cr.status in {ExtractStatus.OK, ExtractStatus.EMPTY}:
                completed.add(chunk.chunk_id)

        if doc_store is not None:
            _persist_doc_provenance(doc_store, doc_provenance, batch_id=batch)
    finally:
        if journal_f is not None:
            journal_f.close()
        if quarantine_f is not None:
            quarantine_f.close()

    return PipelineResult(
        accepted=all_accepted,
        rejected=all_rejected,
        chunk_results=chunk_results,
        completed_chunk_ids=completed,
        failed_chunk_ids=failed_ids,
    )


def _extract_chunk_with_retry(
    chunk: ChunkRecord,
    schema: SchemaDefinition,
    *,
    extract_fn: ExtractFn | None,
    llm: LLMProvider | None,
    confidence_threshold: float,
    retry: RetryPolicy,
    sleep: Callable[[float], None],
    batch_id: str,
) -> ChunkExtractResult:
    t0 = time.perf_counter()
    attempt = 0
    while True:
        attempt += 1
        delay = retry.delay_before_attempt(attempt)
        if delay > 0:
            sleep(delay)
        try:
            if extract_fn is not None:
                raw = extract_fn(chunk, schema)
            else:
                assert llm is not None
                raw = _llm_extract_raw(chunk, schema, llm)
            for t in raw.triples:
                t.source_doc_id = t.source_doc_id or chunk.doc_id
                t.source_chunk_id = t.source_chunk_id or chunk.chunk_id
                _stamp_provenance(t, chunk, batch_id=batch_id)
            gated = gate_triples(raw.triples, schema, confidence_threshold=confidence_threshold)
            status = ExtractStatus.OK if gated.accepted or gated.rejected else ExtractStatus.EMPTY
            if not raw.triples:
                status = ExtractStatus.EMPTY
            return ChunkExtractResult(
                chunk_id=chunk.chunk_id,
                doc_id=chunk.doc_id,
                status=status,
                accepted=gated.accepted,
                rejected=gated.rejected,
                attempts=attempt,
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )
        except Exception as exc:
            if not retry.should_retry(attempt, exc):
                return ChunkExtractResult(
                    chunk_id=chunk.chunk_id,
                    doc_id=chunk.doc_id,
                    status=ExtractStatus.FAILED,
                    attempts=attempt,
                    error=f"{type(exc).__name__}: {exc}",
                    elapsed_ms=int((time.perf_counter() - t0) * 1000),
                )
            # else loop


def _llm_extract_raw(
    chunk: ChunkRecord, schema: SchemaDefinition, llm: LLMProvider
) -> ExtractResult:
    prompt_template = load_prompt("extract")
    system, user = _split_prompt(
        prompt_template.format(
            schema_summary=schema.summary(),
            doc_id=chunk.doc_id,
            chunk_id=chunk.chunk_id,
            chunk_text=chunk.text,
        )
    )
    messages = [
        Message(role="system", content=system),
        Message(role="user", content=user),
    ]
    return complete_structured(llm, messages, ExtractResult, tier=Tier.STRONG)


def _stamp_provenance(triple: Triple, chunk: ChunkRecord, *, batch_id: str | None = None) -> None:
    """Attach source metadata for FR-KG-01/02 audit trail."""
    attrs = dict(triple.attributes or {})
    attrs.setdefault("source_doc_id", chunk.doc_id)
    attrs.setdefault("source_chunk_id", chunk.chunk_id)
    if batch_id:
        attrs["extract_batch_id"] = batch_id
    title = (chunk.metadata or {}).get("title")
    if title:
        attrs.setdefault("source_title", title)
    path = (chunk.metadata or {}).get("source_path") or (chunk.metadata or {}).get("filename")
    if path:
        attrs.setdefault("source_path", path)
    triple.attributes = attrs


def _persist_doc_provenance(
    doc_store: DocStore,
    by_doc: dict[str, list[dict[str, Any]]],
    *,
    batch_id: str,
) -> None:
    """Merge extract outcomes into document metadata when docs are loadable."""
    for doc_id, outcomes in by_doc.items():
        try:
            # FileDocStore may only support save; optional load
            load = getattr(doc_store, "load", None) or getattr(doc_store, "get", None)
            if not callable(load):
                continue
            doc = load(doc_id)
            if doc is None:
                continue
            meta = dict(doc.metadata or {})
            hist = list(meta.get("extract_history") or [])
            hist.append(
                {
                    "batch_id": batch_id,
                    "chunks": outcomes,
                    "ts": datetime.now(UTC).isoformat(),
                }
            )
            meta["extract_history"] = hist[-20:]  # keep last 20 batches
            meta["last_extract_batch_id"] = batch_id
            doc.metadata = meta
            doc_store.save(doc)
        except Exception:
            # Provenance is best-effort; never fail the pipeline
            continue


def _split_prompt(text: str) -> tuple[str, str]:
    if "# System" in text and "# User" in text:
        parts = text.split("# User", 1)
        system = parts[0].replace("# System", "", 1).strip()
        user = parts[1].strip()
        return system, user
    return "You are a knowledge extraction engine.", text
