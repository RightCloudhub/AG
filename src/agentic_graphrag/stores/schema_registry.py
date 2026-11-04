"""Immutable Schema versions and reviewed drafts in tenant/KB-scoped SQLite."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentic_graphrag.core.knowledge import KnowledgeScope
from agentic_graphrag.core.schema_drafts import DraftApproval, SchemaDraft


class SQLiteSchemaRegistry:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self._lock = threading.RLock()
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS schema_drafts (tenant TEXT, kb TEXT, id TEXT, data TEXT, "
            "PRIMARY KEY(tenant,kb,id));"
            "CREATE TABLE IF NOT EXISTS schema_versions "
            "(tenant TEXT, kb TEXT, id TEXT, version TEXT, "
            "data TEXT, PRIMARY KEY(tenant,kb,id,version));"
        )

    def close(self) -> None:
        self._db.close()

    def save_draft(self, draft: SchemaDraft) -> SchemaDraft:
        if draft.status != "draft":
            raise ValueError("New drafts must be unapproved")
        with self._lock, self._db:
            old = self.get_draft(draft.scope, draft.draft_id)
            if old is not None:
                if old.input_fingerprint != draft.input_fingerprint:
                    raise ValueError("Draft identity conflicts with another graph input")
                return old
            self._store(draft)
        return draft

    def _store(self, draft: SchemaDraft) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO schema_drafts VALUES (?,?,?,?)",
            (draft.scope.tenant_id, draft.scope.kb_id, draft.draft_id, draft.model_dump_json()),
        )

    def get_draft(self, scope: KnowledgeScope, draft_id: str) -> SchemaDraft | None:
        with self._lock:
            row = self._db.execute(
                "SELECT data FROM schema_drafts WHERE tenant=? AND kb=? AND id=?",
                (scope.tenant_id, scope.kb_id, draft_id),
            ).fetchone()
        return SchemaDraft.model_validate_json(row[0]) if row else None

    def list_drafts(self, scope: KnowledgeScope) -> list[SchemaDraft]:
        with self._lock:
            rows = self._db.execute(
                "SELECT data FROM schema_drafts WHERE tenant=? AND kb=? ORDER BY id",
                (scope.tenant_id, scope.kb_id),
            ).fetchall()
        return [SchemaDraft.model_validate_json(row[0]) for row in rows]

    def approve(
        self, scope: KnowledgeScope, draft_id: str, *, approval: DraftApproval
    ) -> SchemaDraft:
        with self._lock, self._db:
            draft = self.get_draft(scope, draft_id)
            if draft is None:
                raise KeyError("Unknown draft in this knowledge scope")
            if draft.status != "draft":
                raise ValueError("This draft already has a review decision")
            schema = {
                **approval.schema_definition,
                "name": approval.schema_id,
                "version": approval.schema_version,
            }
            self._write_schema(scope, approval=approval, schema=schema)
            approved = draft.model_copy(
                update={
                    **approval.model_dump(exclude={"schema_definition"}),
                    "schema_definition": schema,
                    "status": "approved",
                    "approved_at": datetime.now(UTC).isoformat(),
                }
            )
            self._store(approved)
        return approved

    def _write_schema(
        self, scope: KnowledgeScope, *, approval: DraftApproval, schema: dict[str, Any]
    ) -> None:
        payload = json.dumps(schema, sort_keys=True)
        old = self.get_schema(scope, schema_id=approval.schema_id, version=approval.schema_version)
        if old is not None and old != schema:
            raise ValueError("A published Schema version is immutable")
        self._db.execute(
            "INSERT OR IGNORE INTO schema_versions VALUES (?,?,?,?,?)",
            (scope.tenant_id, scope.kb_id, approval.schema_id, approval.schema_version, payload),
        )

    def mark_published(self, scope: KnowledgeScope, draft_id: str, *, generation: str) -> None:
        with self._lock, self._db:
            draft = self.get_draft(scope, draft_id)
            if draft is None or draft.status not in {"approved", "published"}:
                raise ValueError("Draft requires human approval before publication")
            self._store(
                draft.model_copy(update={"status": "published", "published_generation": generation})
            )

    def get_schema(
        self, scope: KnowledgeScope, *, schema_id: str, version: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT data FROM schema_versions WHERE tenant=? AND kb=? AND id=? AND version=?",
                (scope.tenant_id, scope.kb_id, schema_id, version),
            ).fetchone()
        return dict(json.loads(row[0])) if row else None
