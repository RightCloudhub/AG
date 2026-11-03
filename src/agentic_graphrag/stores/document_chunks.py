"""SQLite chunk payloads, separate from document text and identity."""

import json
import sqlite3
from dataclasses import asdict

from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord


def create_chunk_table(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS chunks (tenant TEXT, doc_id TEXT, id TEXT, "
        "version INTEGER, position INTEGER, payload TEXT, PRIMARY KEY(tenant,doc_id,id))"
    )


def replace_chunks(db: sqlite3.Connection, doc: DocumentRecord, *, tenant: str) -> None:
    delete_chunks(db, doc.doc_id, tenant=tenant)
    db.executemany(
        "INSERT INTO chunks VALUES (?,?,?,?,?,?)",
        (
            (tenant, doc.doc_id, c.chunk_id, doc.version, pos, json.dumps(asdict(c)))
            for pos, c in enumerate(doc.chunks)
        ),
    )


def checkpoint_chunks(
    db: sqlite3.Connection, doc: DocumentRecord, chunks: list[ChunkRecord], *, tenant: str
) -> None:
    updated = db.executemany(
        "UPDATE chunks SET payload=? WHERE tenant=? AND doc_id=? AND id=? AND version=?",
        ((json.dumps(asdict(c)), tenant, doc.doc_id, c.chunk_id, doc.version) for c in chunks),
    )
    if updated.rowcount != len(chunks):
        raise ValueError("Cannot checkpoint missing or obsolete chunks")


def read_chunks(db: sqlite3.Connection, doc_id: str, *, tenant: str) -> list[ChunkRecord]:
    return [
        ChunkRecord(**json.loads(row[0]))
        for row in db.execute(
            "SELECT payload FROM chunks WHERE tenant=? AND doc_id=? ORDER BY position",
            (tenant, doc_id),
        )
    ]


def delete_chunks(db: sqlite3.Connection, doc_id: str, *, tenant: str) -> None:
    db.execute("DELETE FROM chunks WHERE tenant=? AND doc_id=?", (tenant, doc_id))
