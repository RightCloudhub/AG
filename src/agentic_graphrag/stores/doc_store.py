"""Document adapters with indexed identity and durable chunk progress."""

from __future__ import annotations

import json
import sqlite3
from contextlib import AbstractContextManager, closing
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path

from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord
from agentic_graphrag.core.tenancy import scoped_id, tenant_matches
from agentic_graphrag.stores.doc_artifacts_sql import clear_tombstone, tombstone
from agentic_graphrag.stores.doc_ops_sql import bump_generation
from agentic_graphrag.stores.doc_ops_store import DocOpsMixin
from agentic_graphrag.stores.doc_schema import (
    PROJECTION_NAMES,
    document_projection,
    finish_upgrade,
    prepare_schema,
)
from agentic_graphrag.stores.document_chunks import (
    checkpoint_chunks,
    create_chunk_table,
    delete_chunks,
    read_chunks,
    replace_chunks,
)
from agentic_graphrag.stores.file_lock import FileMutex


def decode_document(payload: str) -> DocumentRecord:
    data = json.loads(payload)
    data["chunks"] = [ChunkRecord(**chunk) for chunk in data.get("chunks", [])]
    data.setdefault("title", "")
    data.setdefault("content", "")
    data["metadata"] = data.get("metadata") or {}
    data["tenant_id"] = data.get("tenant_id") or data["metadata"].get("tenant_id", "")
    return DocumentRecord(**data)


def _tenant(doc: DocumentRecord) -> str:
    return doc.tenant_id or str(doc.metadata.get("tenant_id") or "default")


class InMemoryDocStore:
    durable = False

    def __init__(self) -> None:
        self._docs: dict[str, DocumentRecord] = {}
        self._positions: dict[str, dict[str, int]] = {}
        self._mutex = FileMutex()

    def lock(self) -> AbstractContextManager[None]:
        return self._mutex.hold()

    def save(self, doc: DocumentRecord) -> None:
        key = scoped_id(_tenant(doc), doc.doc_id)
        self._docs[key] = deepcopy(doc)
        self._positions[key] = {c.chunk_id: pos for pos, c in enumerate(doc.chunks)}

    def save_chunks(self, doc: DocumentRecord, chunks: list[ChunkRecord]) -> None:
        key = scoped_id(_tenant(doc), doc.doc_id)
        stored = self._docs.get(key)
        positions = self._positions.get(key, {})
        if stored is None or stored.version != doc.version:
            raise ValueError("Cannot checkpoint missing or obsolete document")
        if any(c.chunk_id not in positions for c in chunks):
            raise ValueError("Cannot checkpoint missing chunks")
        for chunk in chunks:
            if stored.chunks[positions[chunk.chunk_id]].revision <= chunk.revision:
                stored.chunks[positions[chunk.chunk_id]] = deepcopy(chunk)

    def get(self, doc_id: str, *, tenant_id: str | None = None) -> DocumentRecord | None:
        matches = [
            d
            for d in self._docs.values()
            if d.doc_id == doc_id and tenant_matches(_tenant(d), tenant_id)
        ]
        return decode_document(json.dumps(asdict(matches[0]))) if len(matches) == 1 else None

    def list_refs(self, *, tenant_id: str | None = None) -> list[tuple[str, str]]:
        return sorted(
            (d.doc_id, _tenant(d))
            for d in self._docs.values()
            if tenant_matches(_tenant(d), tenant_id)
        )

    def list_ids(self, *, tenant_id: str | None = None) -> list[str]:
        return sorted({did for did, _ in self.list_refs(tenant_id=tenant_id)})

    def delete(self, doc_id: str, *, tenant_id: str) -> None:
        self._docs.pop(scoped_id(tenant_id, doc_id), None)
        self._positions.pop(scoped_id(tenant_id, doc_id), None)

    def find(self, value: str, *, tenant_id: str, by_hash: bool = False) -> DocumentRecord | None:
        field = "content_sha256" if by_hash else "source_uri"
        return next(
            (
                self.get(d.doc_id, tenant_id=tenant_id)
                for d in self._docs.values()
                if tenant_matches(_tenant(d), tenant_id) and getattr(d, field) == value
            ),
            None,
        )


class FileDocStore(DocOpsMixin):
    """SQLite identity index and document payloads; imports legacy JSON once.

    Identity and chunk progress commit together. Imported paths are tracked so
    deleted documents cannot be resurrected from legacy migration input. Schema v2
    adds read projections, chunk revisions and durable operations (``doc_schema``).
    """

    durable = True

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "documents.sqlite3"
        self._mutex = FileMutex(self.root / ".documents.lock")
        self._legacy_stamp: tuple[tuple[str, int], ...] = ()
        with self.lock(), closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS documents (tenant TEXT, id TEXT, "
                "source TEXT, hash TEXT, payload TEXT, PRIMARY KEY(tenant,id))"
            )
            db.execute("CREATE INDEX IF NOT EXISTS doc_source ON documents(tenant,source)")
            db.execute("CREATE INDEX IF NOT EXISTS doc_hash ON documents(tenant,hash)")
            db.execute("CREATE INDEX IF NOT EXISTS doc_id ON documents(id)")
            db.execute("CREATE TABLE IF NOT EXISTS imported (path TEXT PRIMARY KEY)")
            create_chunk_table(db)
            prepare_schema(db)
            self._upgrade_chunks(db)
            finish_upgrade(db)
        self._import_legacy()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=60)

    def lock(self) -> AbstractContextManager[None]:
        return self._mutex.hold()

    def save(self, doc: DocumentRecord) -> None:
        with closing(self._connect()) as db, db:
            self._save(db, doc)

    def save_chunks(self, doc: DocumentRecord, chunks: list[ChunkRecord]) -> None:
        with closing(self._connect()) as db, db:
            checkpoint_chunks(db, doc, chunks, tenant=_tenant(doc))

    def _upgrade_chunks(self, db: sqlite3.Connection) -> None:
        if db.execute("PRAGMA user_version").fetchone()[0] >= 1:
            return
        refs = db.execute("SELECT tenant,id FROM documents").fetchall()
        for tenant, did in refs:
            payload = db.execute(
                "SELECT payload FROM documents WHERE tenant=? AND id=?", (tenant, did)
            ).fetchone()[0]
            self._save(db, decode_document(payload))
        db.execute("PRAGMA user_version=1")

    @staticmethod
    def _save(db: sqlite3.Connection, doc: DocumentRecord) -> None:
        names = ",".join(("tenant", "id", "source", "hash", "payload", *PROJECTION_NAMES))
        db.execute(
            f"INSERT OR REPLACE INTO documents ({names}) VALUES "
            f"({','.join('?' * (5 + len(PROJECTION_NAMES)))})",
            (
                _tenant(doc),
                doc.doc_id,
                doc.source_uri,
                doc.content_sha256,
                json.dumps(asdict(replace(doc, chunks=[])), ensure_ascii=False),
                *document_projection(doc),
            ),
        )
        replace_chunks(db, doc, tenant=_tenant(doc))
        clear_tombstone(db, _tenant(doc), doc.doc_id)

    def _legacy_paths(self) -> list[Path]:
        return [*self.root.glob("*.json"), *(self.root / "v2").glob("*.json")]

    def _import_legacy(self, *, force: bool = False) -> None:
        """Import legacy JSON payloads; reads without such files never take the lock.

        The stamp covers only ``*.json`` inputs: SQLite journal files change the
        directory mtime on every write, which used to make ordinary reads wait on
        the document mutex held by indexing.
        """
        paths = self._legacy_paths()
        stamp = tuple(sorted((str(p), p.stat().st_mtime_ns) for p in paths))
        if not paths or (not force and stamp == self._legacy_stamp):
            self._legacy_stamp = stamp
            return
        with self.lock(), closing(self._connect()) as db, db:
            imported = {r[0] for r in db.execute("SELECT path FROM imported")}
            for path in paths:
                if str(path) not in imported:
                    self._save(db, decode_document(path.read_text(encoding="utf-8")))
                    db.execute("INSERT INTO imported VALUES (?)", (str(path),))
        self._legacy_stamp = stamp

    def _lookup(self, sql: str, params: tuple[str, ...]) -> DocumentRecord | None:
        self._import_legacy()
        with closing(self._connect()) as db:
            # Read the identity and its chunks from one SQLite snapshot without
            # holding the indexing/lifecycle lock throughout ordinary reads.
            db.execute("BEGIN")
            rows = db.execute(sql, params).fetchall()
            if not rows:
                db.rollback()
                self._import_legacy(force=True)
                db.execute("BEGIN")
                rows = db.execute(sql, params).fetchall()
            if len(rows) != 1:
                return None
            doc = decode_document(rows[0][0])
            doc.chunks = read_chunks(db, doc.doc_id, tenant=_tenant(doc))
            return doc

    def get(self, doc_id: str, *, tenant_id: str | None = None) -> DocumentRecord | None:
        sql = "SELECT payload FROM documents WHERE id=?"
        params: tuple[str, ...] = (doc_id,)
        if tenant_id is not None:
            sql, params = sql + " AND tenant=?", (*params, tenant_id or "default")
        return self._lookup(sql, params)

    def find(self, value: str, *, tenant_id: str, by_hash: bool = False) -> DocumentRecord | None:
        column = "hash" if by_hash else "source"
        return self._lookup(
            f"SELECT payload FROM documents WHERE tenant=? AND {column}=? LIMIT 1",
            (tenant_id or "default", value),
        )

    def list_refs(self, *, tenant_id: str | None = None) -> list[tuple[str, str]]:
        self._import_legacy()
        sql = "SELECT id,tenant FROM documents"
        params: tuple[str, ...] = ()
        if tenant_id is not None:
            sql, params = sql + " WHERE tenant=?", (tenant_id or "default",)
        with closing(self._connect()) as db:
            return [(str(r[0]), str(r[1])) for r in db.execute(sql + " ORDER BY id", params)]

    def list_ids(self, *, tenant_id: str | None = None) -> list[str]:
        return sorted({did for did, _ in self.list_refs(tenant_id=tenant_id)})

    def delete(self, doc_id: str, *, tenant_id: str) -> None:
        """Delete identity and chunks; a tombstone keeps stale index hits invisible."""
        tenant = tenant_id or "default"
        with closing(self._connect()) as db, db:
            delete_chunks(db, doc_id, tenant=tenant)
            db.execute("DELETE FROM documents WHERE id=? AND tenant=?", (doc_id, tenant))
            # Fence any worker still holding this document's lease.
            db.execute("DELETE FROM doc_state WHERE tenant=? AND doc_id=?", (tenant, doc_id))
            tombstone(db, tenant, doc_id)
            bump_generation(db, tenant)
