"""Durable scoped evidence and retrieval audit records, independent of Web identity."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agentic_graphrag.core.knowledge import KnowledgeScope


class SourceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS source_artifacts (
                tenant TEXT NOT NULL, kb TEXT NOT NULL, kind TEXT NOT NULL,
                id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(tenant, kb, kind, id))""")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, scope: KnowledgeScope, record: dict[str, Any], *, kind: str, key: str) -> None:
        self.put_many(scope, [(key, record)], kind=kind)

    def put_many(
        self, scope: KnowledgeScope, records: Sequence[tuple[str, dict[str, Any]]], *, kind: str
    ) -> None:
        if not records:
            return
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for key, record in records:
                payload = json.dumps(record, ensure_ascii=False, sort_keys=True)
                existing = db.execute(
                    "SELECT payload FROM source_artifacts "
                    "WHERE tenant=? AND kb=? AND kind=? AND id=?",
                    (scope.tenant_id, scope.kb_id, kind, key),
                ).fetchone()
                if existing and existing[0] != payload:
                    raise ValueError("An immutable source artifact cannot be overwritten")
                db.execute(
                    "INSERT OR IGNORE INTO source_artifacts VALUES (?, ?, ?, ?, ?)",
                    (scope.tenant_id, scope.kb_id, kind, key, payload),
                )

    def get(self, scope: KnowledgeScope, key: str, *, kind: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM source_artifacts WHERE tenant=? AND kb=? AND kind=? AND id=?",
                (scope.tenant_id, scope.kb_id, kind, key),
            ).fetchone()
        return dict(json.loads(row[0])) if row else None
