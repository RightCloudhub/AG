"""Typed document/chunk read models for the operations API (K-01).

Field names are a frozen contract for the Web UI. Index states are honest: a
channel without a backing capability reports ``unavailable`` (an offline profile
without a semantic embedder never shows a ready vector index).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel

from agentic_graphrag.core.ports.document_ops import ACTIVE_OP_STATES, DocOperation, OpKind
from agentic_graphrag.core.ports.stores import INDEX_STAGES

IndexState = Literal["pending", "running", "partial", "ready", "failed", "disabled", "unavailable"]
_ERROR_PREFIX = {"vector": "embedding_", "fulltext": "fulltext_", "graph": "graph_"}
_PROVENANCE_KEYS = ("parser_name", "parser_version", "source_mime_type", "tokenizer")
_PARSE_KINDS = (OpKind.PARSE.value, OpKind.REPARSE.value)


class ChannelIndexState(BaseModel):
    state: IndexState
    indexed: int
    total: int
    error: str | None = None
    model: str | None = None
    generation: int = 0


class ParseState(BaseModel):
    state: str
    error: str | None = None
    task_id: str | None = None


class ChunkCounts(BaseModel):
    total: int
    enabled: int


class PendingOperation(BaseModel):
    kind: str
    task_id: str | None = None
    state: str


class SourceArtifactOut(BaseModel):
    available: bool
    sha256: str | None = None
    media_type: str | None = None


class DocumentSummary(BaseModel):
    doc_id: str
    title: str
    source: str
    version: int
    created_at: str
    updated_at: str
    parse: ParseState
    chunks: ChunkCounts
    indexes: dict[str, ChannelIndexState]
    pending_operation: PendingOperation | None = None
    source_artifact: SourceArtifactOut


class ChunkStage(BaseModel):
    state: str
    revision: int | None = None
    error: str | None = None
    model: str | None = None


class ChunkSummary(BaseModel):
    chunk_id: str
    doc_id: str
    ordinal: int
    revision: int
    enabled: bool
    breadcrumb: list[str]
    token_count: int
    text_preview: str
    text_sha256: str
    stages: dict[str, ChunkStage]


class ChunkDetail(ChunkSummary):
    text: str
    provenance: dict[str, Any]


@dataclass(frozen=True)
class Availability:
    vector: bool = False
    fulltext: bool = True
    graph: bool = True

    def of(self, stage: str) -> bool:
        return bool(getattr(self, stage))


def public_source(uri: str) -> str:
    """Never expose server filesystem paths: ``file://`` URIs keep only the file name."""
    if uri.startswith("file://"):
        return "file://" + PurePosixPath(uri[len("file://") :]).name
    return uri


def channel_state(
    available: bool, counts: tuple[int, int, int], *, error: str | None, running: bool
) -> IndexState:
    total_chunks, enabled, indexed = counts
    if not available:
        return "unavailable"
    if total_chunks and not enabled:
        return "disabled"
    if enabled and indexed >= enabled:
        return "ready"
    if running:
        return "running"
    if error:
        return "partial" if indexed else "failed"
    return "partial" if indexed else "pending"


def _channel_error(row: dict[str, Any], stage: str) -> str | None:
    chunk_error = row["stages"][stage]["error"]
    doc_error = row["index_error"] or ""
    return chunk_error or (doc_error if doc_error.startswith(_ERROR_PREFIX[stage]) else None)


def _latest(ops: list[DocOperation], kinds: tuple[str, ...] | None = None) -> DocOperation | None:
    chosen = [op for op in ops if kinds is None or op.kind in kinds]
    return chosen[-1] if chosen else None


def pending_operation(row: dict[str, Any], ops: list[DocOperation]) -> PendingOperation | None:
    if row["reserved"]:
        if row["lifecycle"] == "failed":
            return None
        return PendingOperation(kind=OpKind.PARSE.value, task_id=row["task_id"], state="pending")
    active = _latest([op for op in ops if op.state in ACTIVE_OP_STATES])
    if active is not None:
        return PendingOperation(kind=active.kind, task_id=active.task_id, state=active.state)
    if row["lifecycle"]:
        kind = "delete" if row["lifecycle"] == "deleting" else "replace"
        return PendingOperation(kind=kind, state="pending")
    return None


def parse_state(row: dict[str, Any], ops: list[DocOperation]) -> ParseState:
    if row["reserved"]:
        state = {"failed": "failed", "running": "running"}.get(row["lifecycle"], "queued")
        return ParseState(state=state, error=row["index_error"] or None, task_id=row["task_id"])
    latest = _latest(ops, _PARSE_KINDS)
    if latest is None or latest.state == "done":
        return ParseState(state="ready", task_id=row["task_id"] or None)
    state = {"pending": "queued", "running": "running"}.get(latest.state, "failed")
    error = latest.error or None if state == "failed" else None
    return ParseState(state=state, error=error, task_id=latest.task_id)


def document_summary(
    row: dict[str, Any],
    *,
    ops: list[DocOperation],
    source: tuple[str, str] | None,
    availability: Availability,
) -> DocumentSummary:
    running = any(op.state == "running" for op in ops)
    indexes = {
        stage: ChannelIndexState(
            state=channel_state(
                availability.of(stage),
                (row["total"], row["enabled"], row["stages"][stage]["indexed"]),
                error=_channel_error(row, stage),
                running=running,
            ),
            indexed=row["stages"][stage]["indexed"],
            total=row["enabled"],
            error=_channel_error(row, stage),
            model=row["model"] if stage == "vector" else None,
            generation=row["stages"][stage]["generation"],
        )
        for stage in INDEX_STAGES
    }
    return DocumentSummary(
        doc_id=row["doc_id"],
        title=row["title"],
        source=public_source(row["source"]),
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        parse=parse_state(row, ops),
        chunks=ChunkCounts(total=row["total"], enabled=row["enabled"]),
        indexes=indexes,
        pending_operation=pending_operation(row, ops),
        source_artifact=SourceArtifactOut(
            available=source is not None,
            sha256=source[0] if source else None,
            media_type=(source[1] or None) if source else None,
        ),
    )


def chunk_stage(row: dict[str, Any], stage: str, availability: Availability) -> ChunkStage:
    meta = row["stages"].get(stage) or {}
    model = meta.get("model") or (row["model"] if stage == "vector" else None) or None
    if not row["enabled"]:
        state = "disabled"
    elif not availability.of(stage):
        state = "unavailable"
    elif row["flags"][stage]:
        state = "ready"
    else:
        state = "failed" if meta.get("error") else "pending"
    return ChunkStage(
        state=state, revision=meta.get("revision"), error=meta.get("error"), model=model
    )


def chunk_summary(row: dict[str, Any], availability: Availability) -> ChunkSummary:
    return ChunkSummary(
        chunk_id=row["chunk_id"],
        doc_id=row["doc_id"],
        ordinal=row["ordinal"],
        revision=row["revision"],
        enabled=row["enabled"],
        breadcrumb=row["breadcrumb"],
        token_count=row["token_count"],
        text_preview=row["text_preview"],
        text_sha256=row["text_sha256"],
        stages={stage: chunk_stage(row, stage, availability) for stage in INDEX_STAGES},
    )


def chunk_detail(row: dict[str, Any], payload: str, availability: Availability) -> ChunkDetail:
    data = json.loads(payload)
    meta = data.get("metadata") or {}
    provenance = {
        "doc_id": row["doc_id"],
        "doc_version": meta.get("document_version"),
        "chunk_id": row["chunk_id"],
        "ordinal": row["ordinal"],
        "revision": row["revision"],
        "heading_path": list(meta.get("heading_path") or []),
        "edited": row["revision"] > 1,
        **{key: meta[key] for key in _PROVENANCE_KEYS if key in meta},
    }
    summary = chunk_summary(row, availability).model_dump()
    return ChunkDetail(**summary, text=str(data.get("text", "")), provenance=provenance)
