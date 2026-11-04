"""SQLite transaction boundary for core metadata, unrelated to Web identities."""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agentic_graphrag.core.knowledge import KnowledgeBase, KnowledgeScope

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_bases (
    tenant TEXT NOT NULL, kb TEXT NOT NULL, revision INTEGER NOT NULL,
    payload TEXT NOT NULL, PRIMARY KEY (tenant,kb));
CREATE TABLE IF NOT EXISTS write_intents (
    tenant TEXT NOT NULL, kb TEXT NOT NULL, operation TEXT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL,
    PRIMARY KEY (tenant,kb,operation));
CREATE TABLE IF NOT EXISTS graph_changes (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL,
    kb TEXT NOT NULL, operation TEXT NOT NULL, payload TEXT NOT NULL,
    UNIQUE (tenant,kb,operation));
CREATE INDEX IF NOT EXISTS changes_scope ON graph_changes(tenant,kb,sequence);
CREATE TABLE IF NOT EXISTS knowledge_snapshots (
    tenant TEXT NOT NULL, kb TEXT NOT NULL, generation TEXT NOT NULL,
    manifest TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (tenant,kb,generation));
CREATE TABLE IF NOT EXISTS source_tombstones (
    tenant TEXT NOT NULL, kb TEXT NOT NULL, doc_id TEXT NOT NULL,
    through_version INTEGER NOT NULL, PRIMARY KEY (tenant,kb,doc_id));
CREATE TABLE IF NOT EXISTS knowledge_summaries (
    tenant TEXT NOT NULL, kb TEXT NOT NULL, generation TEXT NOT NULL,
    id TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (tenant,kb,generation,id));
"""


def scope_params(scope: KnowledgeScope) -> tuple[str, str]:
    return scope.tenant_id, scope.kb_id


def read_kb(db: sqlite3.Connection, scope: KnowledgeScope) -> KnowledgeBase | None:
    row = db.execute(
        "SELECT payload FROM knowledge_bases WHERE tenant=? AND kb=?", scope_params(scope)
    ).fetchone()
    return KnowledgeBase.model_validate_json(row[0]) if row else None


def write_kb(db: sqlite3.Connection, kb: KnowledgeBase, *, expected_revision: int) -> None:
    cursor = db.execute(
        "UPDATE knowledge_bases SET revision=?,payload=? WHERE tenant=? AND kb=? AND revision=?",
        (kb.config_revision, kb.model_dump_json(), kb.tenant_id, kb.kb_id, expected_revision),
    )
    if cursor.rowcount != 1:
        raise ValueError("Knowledge base configuration revision conflict")


class KnowledgeDatabase:
    durable = True

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path) if str(path) != ":memory:" else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.RLock()
        self._db = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._mutex:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.rollback()
                raise
            else:
                self._db.commit()

    def close(self) -> None:
        with self._mutex:
            self._db.close()
