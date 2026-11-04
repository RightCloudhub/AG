"""Knowledge configuration with compare-and-swap publication and durable journals."""

import sqlite3

from agentic_graphrag.core.knowledge import KnowledgeBase, KnowledgeScope
from agentic_graphrag.stores.knowledge_database import read_kb, write_kb
from agentic_graphrag.stores.knowledge_snapshots import KnowledgeSnapshots


class SQLiteKnowledgeStore(KnowledgeSnapshots):
    def get(self, scope: KnowledgeScope) -> KnowledgeBase | None:
        with self.transaction() as db:
            return read_kb(db, scope)

    def list(self, tenant_id: str, *, limit: int = 100, offset: int = 0) -> list[KnowledgeBase]:
        tenant_id = KnowledgeScope(tenant_id=tenant_id).tenant_id
        with self.transaction() as db:
            rows = db.execute(
                "SELECT payload FROM knowledge_bases WHERE tenant=? ORDER BY kb LIMIT ? OFFSET ?",
                (tenant_id, max(0, limit), max(0, offset)),
            ).fetchall()
        return [KnowledgeBase.model_validate_json(row[0]) for row in rows]

    def create(self, kb: KnowledgeBase) -> KnowledgeBase:
        if kb.config_revision != 1:
            raise ValueError("A new knowledge base starts at configuration revision 1")
        try:
            with self.transaction() as db:
                db.execute(
                    "INSERT INTO knowledge_bases VALUES (?,?,?,?)",
                    (kb.tenant_id, kb.kb_id, kb.config_revision, kb.model_dump_json()),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Knowledge base already exists") from exc
        return kb

    def ensure_default(self, tenant_id: str) -> KnowledgeBase:
        scope = KnowledgeScope(tenant_id=tenant_id)
        with self.transaction() as db:
            kb = read_kb(db, scope)
            if kb is not None:
                return kb
            kb = KnowledgeBase(tenant_id=tenant_id)
            db.execute(
                "INSERT INTO knowledge_bases VALUES (?,?,?,?)",
                (tenant_id, kb.kb_id, kb.config_revision, kb.model_dump_json()),
            )
        return kb

    def update(self, kb: KnowledgeBase, *, expected_revision: int) -> KnowledgeBase:
        with self.transaction() as db:
            current = read_kb(db, kb.scope)
            if current is None:
                raise ValueError("Knowledge base does not exist")
            if (kb.build_mode, kb.active_generation, kb.schema_id, kb.schema_version) != (
                current.build_mode,
                current.active_generation,
                current.schema_id,
                current.schema_version,
            ):
                raise ValueError("Changing graph mode or Schema requires a sealed generation")
            updated = kb.model_copy(update={"config_revision": expected_revision + 1})
            write_kb(db, updated, expected_revision=expected_revision)
        return updated


class InMemoryKnowledgeStore(SQLiteKnowledgeStore):
    """The same transactional contract in process-local SQLite for deterministic tests."""

    durable = False

    def __init__(self) -> None:
        super().__init__(":memory:")
