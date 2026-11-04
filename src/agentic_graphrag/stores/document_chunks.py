"""SQLite chunk payloads plus lightweight projection columns (K-01/K-04).

Payloads keep the full ``ChunkRecord`` (including embeddings); the projection
columns let read models, visibility checks and counts run without decoding them.
Writes never let an older chunk revision overwrite a newer one.
"""

import hashlib
import json
import sqlite3
from dataclasses import asdict
from typing import Any

from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord

PREVIEW_CHARS = 200
CHUNK_COLUMNS = (
    "tenant,doc_id,id,version,position,payload,enabled,revision,text_sha256,token_count,"
    "embedded,fulltext_indexed,graph_extracted,model,summary"
)
_PLACEHOLDERS = ",".join("?" * len(CHUNK_COLUMNS.split(",")))


def create_chunk_table(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS chunks (tenant TEXT, doc_id TEXT, id TEXT, "
        "version INTEGER, position INTEGER, payload TEXT, PRIMARY KEY(tenant,doc_id,id))"
    )


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def breadcrumb_of(chunk: ChunkRecord) -> list[str]:
    title = str(chunk.metadata.get("title") or "")
    path = [str(p) for p in chunk.metadata.get("heading_path") or []]
    return [title, *path] if title else path


def chunk_summary(chunk: ChunkRecord) -> dict[str, Any]:
    return {
        "breadcrumb": breadcrumb_of(chunk),
        "preview": chunk.text[:PREVIEW_CHARS],
        "stages": chunk.stages,
    }


def chunk_row(tenant: str, doc: DocumentRecord, position: int, c: ChunkRecord) -> tuple[Any, ...]:
    return (
        tenant,
        doc.doc_id,
        c.chunk_id,
        doc.version,
        position,
        json.dumps(asdict(c)),
        int(c.enabled),
        int(c.revision),
        text_sha256(c.text),
        int(c.metadata.get("token_count") or 0),
        int(c.embedded),
        int(c.fulltext_indexed),
        int(c.graph_extracted),
        c.embedding_model,
        json.dumps(chunk_summary(c), ensure_ascii=False),
    )


def _stored_revisions(db: sqlite3.Connection, doc: DocumentRecord, tenant: str) -> dict[str, int]:
    rows = db.execute(
        "SELECT id,revision FROM chunks WHERE tenant=? AND doc_id=? AND version=?",
        (tenant, doc.doc_id, doc.version),
    )
    return {str(r[0]): int(r[1]) for r in rows}


def replace_chunks(db: sqlite3.Connection, doc: DocumentRecord, *, tenant: str) -> None:
    """Replace a document's chunks, preserving rows a concurrent edit made newer."""
    stored = _stored_revisions(db, doc, tenant)
    newer = {c.chunk_id for c in doc.chunks if stored.get(c.chunk_id, 0) > c.revision}
    if newer:
        db.execute(
            f"DELETE FROM chunks WHERE tenant=? AND doc_id=? AND id NOT IN "
            f"({','.join('?' * len(newer))})",
            (tenant, doc.doc_id, *sorted(newer)),
        )
    else:
        delete_chunks(db, doc.doc_id, tenant=tenant)
    db.executemany(
        f"INSERT INTO chunks ({CHUNK_COLUMNS}) VALUES ({_PLACEHOLDERS})",
        (
            chunk_row(tenant, doc, pos, c)
            for pos, c in enumerate(doc.chunks)
            if c.chunk_id not in newer
        ),
    )


def checkpoint_chunks(
    db: sqlite3.Connection, doc: DocumentRecord, chunks: list[ChunkRecord], *, tenant: str
) -> list[ChunkRecord]:
    """Update chunk rows at their own revision; returns the chunks actually written.

    A chunk whose stored revision moved on is skipped (edited or disabled meanwhile);
    a missing chunk or an obsolete document version is an error.
    """
    written: list[ChunkRecord] = []
    for c in chunks:
        row = chunk_row(tenant, doc, 0, c)
        cursor = db.execute(
            "UPDATE chunks SET payload=?,enabled=?,text_sha256=?,token_count=?,embedded=?,"
            "fulltext_indexed=?,graph_extracted=?,model=?,summary=? "
            "WHERE tenant=? AND doc_id=? AND id=? AND version=? AND revision=?",
            (*row[5:7], *row[8:], tenant, doc.doc_id, c.chunk_id, doc.version, c.revision),
        )
        if cursor.rowcount == 1:
            written.append(c)
        elif _stored_revisions(db, doc, tenant).get(c.chunk_id, 0) <= c.revision:
            raise ValueError("Cannot checkpoint missing or obsolete chunks")
    return written


def read_chunks(db: sqlite3.Connection, doc_id: str, *, tenant: str) -> list[ChunkRecord]:
    return [
        decode_chunk(row[0])
        for row in db.execute(
            "SELECT payload FROM chunks WHERE tenant=? AND doc_id=? ORDER BY position",
            (tenant, doc_id),
        )
    ]


def decode_chunk(payload: str) -> ChunkRecord:
    return ChunkRecord(**json.loads(payload))


def delete_chunks(db: sqlite3.Connection, doc_id: str, *, tenant: str) -> None:
    db.execute("DELETE FROM chunks WHERE tenant=? AND doc_id=?", (tenant, doc_id))
