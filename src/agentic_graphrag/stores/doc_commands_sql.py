"""Single-transaction command commits: intent + generation + idempotency + pending work.

Each function runs inside ``BEGIN IMMEDIATE`` on the caller's connection, so the
read-check-write sequence is atomic across processes sharing the SQLite file.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from agentic_graphrag.core.ports.document_ops import (
    ChunkPatch,
    CommandOutcome,
    CommandScope,
    DocOperation,
    DocumentBusy,
    Fence,
    OpKind,
    RevisionConflict,
    TargetNotFound,
)
from agentic_graphrag.core.ports.stores import INDEX_STAGES, ChunkRecord, DocumentRecord
from agentic_graphrag.stores import doc_ops_sql as ops
from agentic_graphrag.stores.doc_artifacts_sql import record_history
from agentic_graphrag.stores.document_chunks import chunk_row, decode_chunk

ChunkResponder = Callable[[ChunkRecord, DocOperation], dict[str, Any]]


@dataclass(frozen=True)
class Command:
    scope: CommandScope
    digest: str
    op: DocOperation


def begin_immediate(db: sqlite3.Connection) -> None:
    db.execute("BEGIN IMMEDIATE")


def serving_document(db: sqlite3.Connection, tenant: str, doc_id: str) -> tuple[int, str]:
    row = db.execute(
        "SELECT version,lifecycle FROM documents WHERE tenant=? AND id=?",
        (ops.tenant_key(tenant), doc_id),
    ).fetchone()
    if row is None:
        raise TargetNotFound("Unknown document")
    if row[1]:
        raise DocumentBusy(f"Document has a pending {row[1]} operation")
    return int(row[0]), str(row[1])


def patched_chunk(chunk: ChunkRecord, patch: ChunkPatch) -> ChunkRecord:
    """New revision: text/visibility applied, every stage reset to pending/disabled."""
    text_changed = patch.text is not None and patch.text != chunk.text
    enabled = chunk.enabled if patch.enabled is None else patch.enabled
    metadata = dict(chunk.metadata)
    if text_changed and patch.token_count is not None:
        metadata["token_count"] = patch.token_count
    stages = {
        stage: {
            **(chunk.stages.get(stage) or {}),
            "state": "pending" if enabled else "disabled",
            "error": None,
        }
        for stage in INDEX_STAGES
    }
    return replace(
        chunk,
        text=patch.text if text_changed and patch.text is not None else chunk.text,
        enabled=enabled,
        revision=chunk.revision + 1,
        metadata=metadata,
        stages=stages,
        embedded=False,
        fulltext_indexed=False,
        graph_extracted=False,
        embedding=None if text_changed else chunk.embedding,
        embedding_model="" if text_changed else chunk.embedding_model,
    )


def apply_chunk_patch(
    db: sqlite3.Connection, patch: ChunkPatch, command: Command, respond: ChunkResponder
) -> CommandOutcome:
    begin_immediate(db)
    stored = ops.replay(db, command.scope, command.digest)
    if stored is not None:
        return CommandOutcome(stored, replayed=True)
    version, _ = serving_document(db, patch.tenant_id, patch.doc_id)
    row = db.execute(
        "SELECT payload,position FROM chunks WHERE tenant=? AND doc_id=? AND id=? AND version=?",
        (ops.tenant_key(patch.tenant_id), patch.doc_id, patch.chunk_id, version),
    ).fetchone()
    if row is None:
        raise TargetNotFound("Unknown chunk")
    current = decode_chunk(row[0])
    if current.revision != patch.expected_revision:
        raise RevisionConflict(
            "Chunk revision changed", details={"current_revision": current.revision}
        )
    updated = patched_chunk(current, patch)
    _write_chunk(db, (patch.tenant_id, version, int(row[1])), updated)
    record_history(db, patch.tenant_id, version, current)
    record_history(db, patch.tenant_id, version, updated)
    op = replace(command.op, payload={**command.op.payload, "doc_version": version})
    return _commit(db, command, op, respond(updated, op))


def record_command(
    db: sqlite3.Connection, command: Command, respond: Callable[[DocOperation], dict[str, Any]]
) -> CommandOutcome:
    """Commit a document-level command (reparse) whose preconditions the caller checked."""
    begin_immediate(db)
    stored = ops.replay(db, command.scope, command.digest)
    if stored is not None:
        return CommandOutcome(stored, replayed=True)
    op = command.op
    version, _ = serving_document(db, op.tenant_id, op.doc_id)
    expected = int(op.payload.get("expected_version", version))
    if expected != version:
        raise RevisionConflict("Document version changed", details={"current_version": version})
    seq = ops.mutation_seq(db, op.tenant_id, op.doc_id)
    op = replace(op, payload={**op.payload, "mutation_seq": seq})
    return _commit(db, command, op, respond(op))


def _commit(
    db: sqlite3.Connection, command: Command, op: DocOperation, response: dict[str, Any]
) -> CommandOutcome:
    if op.kind != OpKind.REPARSE:
        ops.bump_mutation(db, op.tenant_id, op.doc_id)
    ops.bump_generation(db, op.tenant_id)
    ops.insert_op(db, op)
    ops.remember(db, command.scope, command.digest, (response, op.op_id))
    return CommandOutcome(response, op=op)


def _write_chunk(db: sqlite3.Connection, where: tuple[str, int, int], chunk: ChunkRecord) -> None:
    tenant, version, position = where
    doc = DocumentRecord(chunk.doc_id, "", "", version=version)
    values = chunk_row(ops.tenant_key(tenant), doc, position, chunk)
    db.execute(
        "UPDATE chunks SET payload=?,enabled=?,revision=?,text_sha256=?,token_count=?,embedded=?,"
        "fulltext_indexed=?,graph_extracted=?,model=?,summary=? "
        "WHERE tenant=? AND doc_id=? AND id=? AND version=?",
        (*values[5:], ops.tenant_key(tenant), chunk.doc_id, chunk.chunk_id, version),
    )


def commit_stage(
    db: sqlite3.Connection, chunk: ChunkRecord, *, fence: Fence, doc: DocumentRecord
) -> bool:
    """Fenced, revision-checked publication of one chunk's stage state."""
    begin_immediate(db)
    if not ops.fence_valid(db, fence):
        return False
    values = chunk_row(ops.tenant_key(doc.tenant_id), doc, 0, chunk)
    cursor = db.execute(
        "UPDATE chunks SET payload=?,enabled=?,text_sha256=?,token_count=?,embedded=?,"
        "fulltext_indexed=?,graph_extracted=?,model=?,summary=? "
        "WHERE tenant=? AND doc_id=? AND id=? AND version=? AND revision=?",
        (*values[5:7], *values[8:], *values[:3], doc.version, chunk.revision),
    )
    if cursor.rowcount != 1:
        return False
    ops.bump_generation(db, doc.tenant_id)
    return True
