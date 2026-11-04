"""SQL for retained sources, staged parses, reservations, tombstones and edit history."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from agentic_graphrag.core.ports.document_ops import SourceArtifact
from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.stores.doc_ops_sql import tenant_key
from agentic_graphrag.stores.document_chunks import text_sha256


def record_source(
    db: sqlite3.Connection, ref: tuple[str, str, int], artifact: SourceArtifact
) -> None:
    tenant, doc_id, version = ref
    db.execute(
        "INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?,?,?)",
        (
            tenant_key(tenant),
            doc_id,
            version,
            artifact.sha256,
            artifact.media_type,
            artifact.filename,
            artifact.size,
            time.time(),
        ),
    )


def source_for(db: sqlite3.Connection, ref: tuple[str, str, int]) -> SourceArtifact | None:
    tenant, doc_id, version = ref
    row = db.execute(
        "SELECT sha256,media_type,filename,size FROM sources WHERE tenant=? AND doc_id=? "
        "AND version=?",
        (tenant_key(tenant), doc_id, version),
    ).fetchone()
    return SourceArtifact(row[0], row[1] or "", row[2] or "", int(row[3] or 0)) if row else None


def stage_content(
    db: sqlite3.Connection,
    op_id: str,
    *,
    ref: tuple[str, str],
    parsed: tuple[str, dict[str, Any]],
) -> None:
    content, metadata = parsed
    db.execute(
        "INSERT OR REPLACE INTO staged VALUES (?,?,?,?,?,?)",
        (op_id, tenant_key(ref[0]), ref[1], content, json.dumps(metadata), time.time()),
    )


def staged_content(db: sqlite3.Connection, op_id: str) -> tuple[str, dict[str, Any]] | None:
    row = db.execute("SELECT content,metadata FROM staged WHERE op_id=?", (op_id,)).fetchone()
    return (str(row[0]), json.loads(row[1] or "{}")) if row else None


def reserve(
    db: sqlite3.Connection, op_id: str, *, ref: tuple[str, str], row: dict[str, str]
) -> None:
    now = time.time()
    db.execute(
        "INSERT OR REPLACE INTO reservations VALUES (?,?,?,?,?,?,?,?,?)",
        (tenant_key(ref[0]), ref[1], op_id, row["title"], row["source"], "queued", "", now, now),
    )


def set_reservations(db: sqlite3.Connection, op_id: str, *, state: str, error: str = "") -> None:
    db.execute(
        "UPDATE reservations SET state=?,error=?,updated_at=? WHERE op_id=?",
        (state, error, time.time(), op_id),
    )


def tombstone(db: sqlite3.Connection, tenant: str, doc_id: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO tombstones VALUES (?,?,?)",
        (tenant_key(tenant), doc_id, time.time()),
    )


def clear_tombstone(db: sqlite3.Connection, tenant: str, doc_id: str) -> None:
    db.execute("DELETE FROM tombstones WHERE tenant=? AND doc_id=?", (tenant_key(tenant), doc_id))


def record_history(
    db: sqlite3.Connection, tenant: str, chunk: ChunkRecord, *, version: int
) -> None:
    db.execute(
        "INSERT OR IGNORE INTO chunk_history VALUES (?,?,?,?,?,?,?,?,?)",
        (
            tenant_key(tenant),
            chunk.doc_id,
            chunk.chunk_id,
            version,
            chunk.revision,
            int(chunk.enabled),
            text_sha256(chunk.text),
            chunk.text,
            time.time(),
        ),
    )
