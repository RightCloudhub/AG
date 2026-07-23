"""Extract pipeline runner: journal, retry, quarantine (P2-KG-01).

Per-chunk task loop with resume-by-journal and failure quarantine.
Single-chunk extract logic lives in
:mod:`agentic_graphrag.knowledge.extract_chunk`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from agentic_graphrag.knowledge.extract_chunk import extract_chunk_with_retry
from agentic_graphrag.knowledge.extract_types import (
    ChunkExtractResult,
    ExtractFn,
    ExtractStatus,
    PipelineResult,
    RetryPolicy,
)
from agentic_graphrag.knowledge.schema_check import SchemaDefinition, Triple
from agentic_graphrag.llm.provider import LLMProvider
from agentic_graphrag.stores.interfaces import ChunkRecord, DocStore

__all__ = [
    "ChunkExtractResult",
    "ExtractFn",
    "ExtractStatus",
    "PipelineResult",
    "RetryPolicy",
    "extract_chunk_with_retry",
    "load_completed_chunk_ids",
    "persist_doc_provenance",
    "run_extract_pipeline",
]

_TEXT_PREVIEW = 300
_HISTORY_KEEP = 20


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


@dataclass
class _PipelineCtx:
    schema: SchemaDefinition
    extract_fn: ExtractFn | None
    llm: LLMProvider | None
    confidence_threshold: float
    retry: RetryPolicy
    sleep: Callable[[float], None]
    batch: str
    journal_f: TextIO | None
    quarantine_f: TextIO | None
    completed: set[str]
    state: _PipelineState


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
    """Taskized per-chunk extraction with journal, retry, quarantine, provenance."""
    if extract_fn is None and llm is None:
        raise ValueError("run_extract_pipeline requires llm or extract_fn")
    ctx = _make_ctx(
        schema,
        llm=llm,
        extract_fn=extract_fn,
        confidence_threshold=confidence_threshold,
        retry=retry or RetryPolicy(),
        journal_path=journal_path,
        quarantine_path=quarantine_path,
        sleep_fn=sleep_fn,
        batch_id=batch_id,
    )
    try:
        for chunk in chunks:
            _process_chunk(chunk, ctx)
        if doc_store is not None:
            persist_doc_provenance(doc_store, ctx.state.doc_provenance, batch_id=ctx.batch)
    finally:
        _close_quiet(ctx.journal_f)
        _close_quiet(ctx.quarantine_f)
    return PipelineResult(
        accepted=ctx.state.all_accepted,
        rejected=ctx.state.all_rejected,
        chunk_results=ctx.state.chunk_results,
        completed_chunk_ids=ctx.completed,
        failed_chunk_ids=ctx.state.failed_ids,
    )


class _PipelineState:
    def __init__(self) -> None:
        self.all_accepted: list[Triple] = []
        self.all_rejected: list[tuple[Triple, str]] = []
        self.chunk_results: list[ChunkExtractResult] = []
        self.failed_ids: set[str] = set()
        self.doc_provenance: dict[str, list[dict[str, Any]]] = {}


def _make_ctx(
    schema: SchemaDefinition,
    *,
    llm: LLMProvider | None,
    extract_fn: ExtractFn | None,
    confidence_threshold: float,
    retry: RetryPolicy,
    journal_path: str | Path | None,
    quarantine_path: str | Path | None,
    sleep_fn: Callable[[float], None] | None,
    batch_id: str | None,
) -> _PipelineCtx:
    return _PipelineCtx(
        schema=schema,
        extract_fn=extract_fn,
        llm=llm,
        confidence_threshold=confidence_threshold,
        retry=retry,
        sleep=sleep_fn or time.sleep,
        batch=batch_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        journal_f=_open_append(journal_path),
        quarantine_f=_open_append(quarantine_path),
        completed=load_completed_chunk_ids(journal_path) if journal_path else set(),
        state=_PipelineState(),
    )


def _process_chunk(chunk: ChunkRecord, ctx: _PipelineCtx) -> None:
    if chunk.chunk_id in ctx.completed:
        ctx.state.chunk_results.append(
            ChunkExtractResult(
                chunk_id=chunk.chunk_id,
                doc_id=chunk.doc_id,
                status=ExtractStatus.SKIPPED,
            )
        )
        return
    cr = extract_chunk_with_retry(
        chunk,
        ctx.schema,
        extract_fn=ctx.extract_fn,
        llm=ctx.llm,
        confidence_threshold=ctx.confidence_threshold,
        retry=ctx.retry,
        sleep=ctx.sleep,
        batch_id=ctx.batch,
    )
    _record_chunk_result(chunk, cr, ctx)


def _record_chunk_result(chunk: ChunkRecord, cr: ChunkExtractResult, ctx: _PipelineCtx) -> None:
    ctx.state.chunk_results.append(cr)
    _write_jsonl(ctx.journal_f, cr.journal_row())
    ctx.state.doc_provenance.setdefault(chunk.doc_id, []).append(cr.journal_row())
    if cr.status == ExtractStatus.FAILED:
        ctx.state.failed_ids.add(chunk.chunk_id)
        _write_jsonl(
            ctx.quarantine_f,
            {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "error": cr.error,
                "attempts": cr.attempts,
                "batch_id": ctx.batch,
                "text_preview": chunk.text[:_TEXT_PREVIEW],
            },
        )
        return
    ctx.state.all_accepted.extend(cr.accepted)
    ctx.state.all_rejected.extend(cr.rejected)
    if cr.status in {ExtractStatus.OK, ExtractStatus.EMPTY}:
        ctx.completed.add(chunk.chunk_id)


def persist_doc_provenance(
    doc_store: DocStore,
    by_doc: dict[str, list[dict[str, Any]]],
    *,
    batch_id: str,
) -> None:
    """Merge extract outcomes into document metadata when docs are loadable."""
    for doc_id, outcomes in by_doc.items():
        try:
            _merge_one_doc(doc_store, doc_id, outcomes, batch_id=batch_id)
        except Exception:
            continue


def _merge_one_doc(
    doc_store: DocStore,
    doc_id: str,
    outcomes: list[dict[str, Any]],
    *,
    batch_id: str,
) -> None:
    load = getattr(doc_store, "load", None) or getattr(doc_store, "get", None)
    if not callable(load):
        return
    doc = load(doc_id)
    if doc is None:
        return
    meta = dict(doc.metadata or {})
    hist = list(meta.get("extract_history") or [])
    hist.append(
        {
            "batch_id": batch_id,
            "chunks": outcomes,
            "ts": datetime.now(UTC).isoformat(),
        }
    )
    meta["extract_history"] = hist[-_HISTORY_KEEP:]
    meta["last_extract_batch_id"] = batch_id
    doc.metadata = meta
    doc_store.save(doc)


def _open_append(path: str | Path | None) -> TextIO | None:
    if path is None:
        return None
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p.open("a", encoding="utf-8")


def _write_jsonl(fh: TextIO | None, row: dict[str, Any]) -> None:
    if fh is None:
        return
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    fh.flush()


def _close_quiet(fh: TextIO | None) -> None:
    if fh is not None:
        fh.close()
