"""Idempotent durable write intents and completion records."""

import sqlite3

from agentic_graphrag.core.knowledge import KnowledgeScope
from agentic_graphrag.core.knowledge_versions import GraphChangeSet, WriteIntent
from agentic_graphrag.stores.knowledge_database import KnowledgeDatabase, scope_params


def _same_operation(old: WriteIntent, new: WriteIntent) -> bool:
    excluded = {"status", "created_at", "stage", "index_generations"}
    return old.model_dump(exclude=excluded) == new.model_dump(exclude=excluded)


def _same_change(old: GraphChangeSet, new: GraphChangeSet) -> bool:
    excluded = {"sequence", "created_at"}
    return old.model_dump(exclude=excluded) == new.model_dump(exclude=excluded)


class KnowledgeJournal(KnowledgeDatabase):
    def begin_write(self, intent: WriteIntent) -> WriteIntent:
        if intent.status != "pending":
            raise ValueError("A new write intent must be pending")
        key = (*scope_params(intent.scope), intent.operation_id)
        with self.transaction() as db:
            row = db.execute(
                "SELECT payload FROM write_intents WHERE tenant=? AND kb=? AND operation=?", key
            ).fetchone()
            if row:
                old = WriteIntent.model_validate_json(row[0])
                if not _same_operation(old, intent):
                    raise ValueError("Operation ID already belongs to another write")
                return old
            db.execute(
                "INSERT INTO write_intents(tenant,kb,operation,payload) VALUES (?,?,?,?)",
                (*key, intent.model_dump_json()),
            )
        return intent

    def get_write(self, scope: KnowledgeScope, operation_id: str) -> WriteIntent | None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT payload FROM write_intents WHERE tenant=? AND kb=? AND operation=?",
                (*scope_params(scope), operation_id),
            ).fetchone()
        return WriteIntent.model_validate_json(row[0]) if row else None

    def mark_seeded(
        self,
        scope: KnowledgeScope,
        operation_id: str,
        *,
        index_generations: tuple[tuple[str, str], ...] = (),
    ) -> WriteIntent:
        with self.transaction() as db:
            return _update_intent(
                db,
                scope,
                operation_id=operation_id,
                stage="seeded",
                index_generations=index_generations,
            )

    def set_write_documents(
        self, scope: KnowledgeScope, operation_id: str, *, document_ids: tuple[str, ...]
    ) -> WriteIntent:
        with self.transaction() as db:
            return _update_intent(db, scope, operation_id=operation_id, document_ids=document_ids)

    def pending_writes(self, scope: KnowledgeScope) -> list[WriteIntent]:
        with self.transaction() as db:
            rows = db.execute(
                "SELECT payload FROM write_intents WHERE tenant=? AND kb=? AND completed=0 "
                "ORDER BY rowid",
                scope_params(scope),
            ).fetchall()
        return [WriteIntent.model_validate_json(row[0]) for row in rows]

    def complete_write(self, change: GraphChangeSet) -> GraphChangeSet:
        with self.transaction() as db:
            return complete_record(db, change)

    def changes(
        self, scope: KnowledgeScope, *, after_sequence: int = 0, limit: int = 100
    ) -> list[GraphChangeSet]:
        with self.transaction() as db:
            rows = db.execute(
                "SELECT payload FROM graph_changes WHERE tenant=? AND kb=? AND sequence>? "
                "ORDER BY sequence LIMIT ?",
                (*scope_params(scope), max(0, after_sequence), max(0, limit)),
            ).fetchall()
        return [GraphChangeSet.model_validate_json(row[0]) for row in rows]


def _validate_completion(intent: WriteIntent, change: GraphChangeSet) -> None:
    if (intent.source_generation, intent.target_generation) != (
        change.source_generation,
        change.target_generation,
    ):
        raise ValueError("Completion generations differ from the persisted intent")
    if set(change.document_ids) != set(intent.document_ids):
        raise ValueError("Completion document selection differs from the persisted intent")


def complete_record(db: sqlite3.Connection, change: GraphChangeSet) -> GraphChangeSet:
    key = (*scope_params(change.scope), change.operation_id)
    existing = db.execute(
        "SELECT payload FROM graph_changes WHERE tenant=? AND kb=? AND operation=?", key
    ).fetchone()
    if existing:
        old = GraphChangeSet.model_validate_json(existing[0])
        if not _same_change(old, change):
            raise ValueError("Completed operation cannot change its payload")
        return old
    row = db.execute(
        "SELECT payload FROM write_intents WHERE tenant=? AND kb=? AND operation=?", key
    ).fetchone()
    if not row:
        raise ValueError("Write must have a persisted intent before completion")
    intent = WriteIntent.model_validate_json(row[0])
    _validate_completion(intent, change)
    cursor = db.execute(
        "INSERT INTO graph_changes(tenant,kb,operation,payload) VALUES (?,?,?,?)",
        (*key, change.model_dump_json()),
    )
    result = change.model_copy(update={"sequence": int(cursor.lastrowid or 0)})
    db.execute(
        "UPDATE graph_changes SET payload=? WHERE sequence=?",
        (result.model_dump_json(), result.sequence),
    )
    db.execute(
        "UPDATE write_intents SET completed=1,payload=? WHERE tenant=? AND kb=? AND operation=?",
        (intent.model_copy(update={"status": "completed"}).model_dump_json(), *key),
    )
    return result


def _update_intent(
    db: sqlite3.Connection,
    scope: KnowledgeScope,
    *,
    operation_id: str,
    stage: str | None = None,
    document_ids: tuple[str, ...] | None = None,
    index_generations: tuple[tuple[str, str], ...] | None = None,
) -> WriteIntent:
    key = (*scope_params(scope), operation_id)
    row = db.execute(
        "SELECT payload FROM write_intents WHERE tenant=? AND kb=? AND operation=?", key
    ).fetchone()
    if not row:
        raise ValueError("Write intent does not exist")
    intent = WriteIntent.model_validate_json(row[0])
    if intent.status == "completed":
        raise ValueError("Completed write intent cannot be modified")
    updates = intent.model_dump()
    if stage is not None:
        updates["stage"] = stage
    if document_ids is not None:
        updates["document_ids"] = sorted(set(document_ids))
    if index_generations is not None:
        updates["index_generations"] = index_generations
    result = WriteIntent.model_validate(updates)
    db.execute(
        "UPDATE write_intents SET payload=? WHERE tenant=? AND kb=? AND operation=?",
        (result.model_dump_json(), *key),
    )
    return result
