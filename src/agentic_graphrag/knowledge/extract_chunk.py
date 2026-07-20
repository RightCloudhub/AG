"""Single-chunk extract with retry (P2-KG-01)."""

from __future__ import annotations

import time
from collections.abc import Callable

from agentic_graphrag.knowledge.extract_core import llm_extract_raw, stamp_provenance
from agentic_graphrag.knowledge.extract_types import (
    ChunkExtractResult,
    ExtractFn,
    ExtractStatus,
    RetryPolicy,
)
from agentic_graphrag.knowledge.schema_check import (
    ExtractResult,
    SchemaDefinition,
    Triple,
    gate_triples,
)
from agentic_graphrag.llm.provider import LLMProvider
from agentic_graphrag.stores.interfaces import ChunkRecord

_MS_PER_S = 1000


def extract_chunk_with_retry(
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
        result = _try_once(
            chunk,
            schema,
            extract_fn=extract_fn,
            llm=llm,
            confidence_threshold=confidence_threshold,
            retry=retry,
            sleep=sleep,
            batch_id=batch_id,
            attempt=attempt,
            t0=t0,
        )
        if result is not None:
            return result


def _try_once(
    chunk: ChunkRecord,
    schema: SchemaDefinition,
    *,
    extract_fn: ExtractFn | None,
    llm: LLMProvider | None,
    confidence_threshold: float,
    retry: RetryPolicy,
    sleep: Callable[[float], None],
    batch_id: str,
    attempt: int,
    t0: float,
) -> ChunkExtractResult | None:
    delay = retry.delay_before_attempt(attempt)
    if delay > 0:
        sleep(delay)
    try:
        return _attempt_extract(
            chunk,
            schema,
            extract_fn=extract_fn,
            llm=llm,
            confidence_threshold=confidence_threshold,
            batch_id=batch_id,
            attempt=attempt,
            t0=t0,
        )
    except Exception as exc:
        if retry.should_retry(attempt, exc):
            return None
        return _failed_result(chunk, attempt=attempt, exc=exc, t0=t0)


def _attempt_extract(
    chunk: ChunkRecord,
    schema: SchemaDefinition,
    *,
    extract_fn: ExtractFn | None,
    llm: LLMProvider | None,
    confidence_threshold: float,
    batch_id: str,
    attempt: int,
    t0: float,
) -> ChunkExtractResult:
    raw = _raw_extract(chunk, schema, extract_fn=extract_fn, llm=llm)
    _stamp_all(raw, chunk, batch_id)
    gated = gate_triples(raw.triples, schema, confidence_threshold=confidence_threshold)
    status = _status_for(raw, gated.accepted, gated.rejected)
    return ChunkExtractResult(
        chunk_id=chunk.chunk_id,
        doc_id=chunk.doc_id,
        status=status,
        accepted=gated.accepted,
        rejected=gated.rejected,
        attempts=attempt,
        elapsed_ms=int((time.perf_counter() - t0) * _MS_PER_S),
    )


def _raw_extract(
    chunk: ChunkRecord,
    schema: SchemaDefinition,
    *,
    extract_fn: ExtractFn | None,
    llm: LLMProvider | None,
) -> ExtractResult:
    if extract_fn is not None:
        return extract_fn(chunk, schema)
    assert llm is not None
    return llm_extract_raw(chunk, schema, llm)


def _stamp_all(raw: ExtractResult, chunk: ChunkRecord, batch_id: str) -> None:
    for t in raw.triples:
        t.source_doc_id = t.source_doc_id or chunk.doc_id
        t.source_chunk_id = t.source_chunk_id or chunk.chunk_id
        stamp_provenance(t, chunk, batch_id=batch_id)


def _status_for(
    raw: ExtractResult,
    accepted: list[Triple],
    rejected: list[tuple[Triple, str]],
) -> ExtractStatus:
    if not raw.triples:
        return ExtractStatus.EMPTY
    if accepted or rejected:
        return ExtractStatus.OK
    return ExtractStatus.EMPTY


def _failed_result(
    chunk: ChunkRecord,
    *,
    attempt: int,
    exc: BaseException,
    t0: float,
) -> ChunkExtractResult:
    return ChunkExtractResult(
        chunk_id=chunk.chunk_id,
        doc_id=chunk.doc_id,
        status=ExtractStatus.FAILED,
        attempts=attempt,
        error=f"{type(exc).__name__}: {exc}",
        elapsed_ms=int((time.perf_counter() - t0) * _MS_PER_S),
    )
