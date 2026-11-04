"""FileDocStore SQLite schema and the one-way v2 migration (K-01/K-04/K-06).

Version 2 adds projection columns and the operation tables. Positional inserts of
older writers fail against the widened tables, and this module refuses to open a
store written by a newer schema, so incompatible writers cannot mix (RUNBOOK).
"""

from __future__ import annotations

import sqlite3

from agentic_graphrag.core.ports.stores import DocumentRecord

SCHEMA_VERSION = 2

_DOCUMENT_COLUMNS = (
    ("title", "TEXT DEFAULT ''"),
    ("version", "INTEGER DEFAULT 1"),
    ("created_at", "TEXT DEFAULT ''"),
    ("updated_at", "TEXT DEFAULT ''"),
    ("lifecycle", "TEXT DEFAULT ''"),
    ("index_error", "TEXT DEFAULT ''"),
    ("index_complete", "INTEGER DEFAULT 0"),
    ("task_id", "TEXT DEFAULT ''"),
)
_CHUNK_COLUMNS = (
    ("enabled", "INTEGER DEFAULT 1"),
    ("revision", "INTEGER DEFAULT 1"),
    ("text_sha256", "TEXT DEFAULT ''"),
    ("token_count", "INTEGER DEFAULT 0"),
    ("embedded", "INTEGER DEFAULT 0"),
    ("fulltext_indexed", "INTEGER DEFAULT 0"),
    ("graph_extracted", "INTEGER DEFAULT 0"),
    ("model", "TEXT DEFAULT ''"),
    ("summary", "TEXT DEFAULT '{}'"),
)
_TABLES = (
    "CREATE TABLE IF NOT EXISTS generations (tenant TEXT PRIMARY KEY, generation INTEGER)",
    "CREATE TABLE IF NOT EXISTS doc_state (tenant TEXT, doc_id TEXT, "
    "mutation_seq INTEGER DEFAULT 0, lease_owner TEXT DEFAULT '', "
    "lease_token INTEGER DEFAULT 0, lease_expires REAL DEFAULT 0, PRIMARY KEY(tenant,doc_id))",
    "CREATE TABLE IF NOT EXISTS ops (id TEXT PRIMARY KEY, tenant TEXT, doc_id TEXT, "
    "kind TEXT, task_id TEXT, payload TEXT, state TEXT, dispatched INTEGER DEFAULT 0, "
    "error TEXT DEFAULT '', created_at REAL, updated_at REAL)",
    "CREATE INDEX IF NOT EXISTS ops_dispatch ON ops(dispatched,created_at)",
    "CREATE INDEX IF NOT EXISTS ops_doc ON ops(tenant,doc_id,created_at)",
    "CREATE TABLE IF NOT EXISTS idempotency (tenant TEXT, operation TEXT, target TEXT, "
    "key TEXT, hash TEXT, response TEXT, op_id TEXT, created_at REAL, "
    "PRIMARY KEY(tenant,operation,target,key))",
    "CREATE TABLE IF NOT EXISTS sources (tenant TEXT, doc_id TEXT, version INTEGER, "
    "sha256 TEXT, media_type TEXT, filename TEXT, size INTEGER, created_at REAL, "
    "PRIMARY KEY(tenant,doc_id,version))",
    "CREATE TABLE IF NOT EXISTS staged (op_id TEXT PRIMARY KEY, tenant TEXT, doc_id TEXT, "
    "content TEXT, metadata TEXT, created_at REAL)",
    "CREATE TABLE IF NOT EXISTS reservations (tenant TEXT, doc_id TEXT, op_id TEXT, "
    "title TEXT, source TEXT, state TEXT, error TEXT DEFAULT '', created_at REAL, "
    "updated_at REAL, PRIMARY KEY(tenant,doc_id,op_id))",
    "CREATE TABLE IF NOT EXISTS tombstones (tenant TEXT, doc_id TEXT, deleted_at REAL, "
    "PRIMARY KEY(tenant,doc_id))",
    "CREATE TABLE IF NOT EXISTS chunk_history (tenant TEXT, doc_id TEXT, chunk_id TEXT, "
    "version INTEGER, revision INTEGER, enabled INTEGER, text_sha256 TEXT, text TEXT, "
    "created_at REAL, PRIMARY KEY(tenant,doc_id,chunk_id,version,revision))",
)


def document_projection(doc: DocumentRecord) -> tuple[object, ...]:
    meta = doc.metadata
    return (
        doc.title,
        doc.version,
        doc.created_at,
        doc.updated_at,
        str(meta.get("lifecycle") or ""),
        str(meta.get("index_error") or ""),
        int(bool(meta.get("index_complete"))),
        str(meta.get("task_id") or ""),
    )


PROJECTION_NAMES = tuple(name for name, _ in _DOCUMENT_COLUMNS)


def prepare_schema(db: sqlite3.Connection) -> None:
    """Refuse newer stores, then add v2 columns/tables (idempotent, cheap)."""
    current = int(db.execute("PRAGMA user_version").fetchone()[0])
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Doc store schema v{current} is newer than this writer (v{SCHEMA_VERSION}); "
            "upgrade the application before writing"
        )
    for statement in _TABLES:
        db.execute(statement)
    _add_columns(db, "documents", _DOCUMENT_COLUMNS)
    _add_columns(db, "chunks", _CHUNK_COLUMNS)


def finish_upgrade(db: sqlite3.Connection) -> None:
    """Backfill projections of rows written before v2, once."""
    if int(db.execute("PRAGMA user_version").fetchone()[0]) >= SCHEMA_VERSION:
        return
    _backfill(db)
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _add_columns(db: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    for name, spec in columns:
        if name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {spec}")


def _backfill(db: sqlite3.Connection) -> None:
    from agentic_graphrag.stores.doc_store import decode_document
    from agentic_graphrag.stores.document_chunks import chunk_row, decode_chunk

    assignments = ",".join(f"{name}=?" for name in PROJECTION_NAMES)
    for tenant, did, payload in db.execute("SELECT tenant,id,payload FROM documents").fetchall():
        doc = decode_document(payload)
        db.execute(
            f"UPDATE documents SET {assignments} WHERE tenant=? AND id=?",
            (*document_projection(doc), tenant, did),
        )
    rows = db.execute("SELECT tenant,doc_id,id,version,payload FROM chunks").fetchall()
    for tenant, did, cid, version, payload in rows:
        chunk = decode_chunk(payload)
        values = chunk_row(tenant, DocumentRecord(did, "", "", version=version), chunk)
        db.execute(
            "UPDATE chunks SET enabled=?,revision=?,text_sha256=?,token_count=?,embedded=?,"
            "fulltext_indexed=?,graph_extracted=?,model=?,summary=? "
            "WHERE tenant=? AND doc_id=? AND id=?",
            (*values[6:], tenant, did, cid),
        )
