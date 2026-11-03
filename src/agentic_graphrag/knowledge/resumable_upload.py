"""Bounded, tenant-owned upload sessions and idempotent parts on a local volume."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentic_graphrag.config_ingest import IndexingConfig
from agentic_graphrag.stores.file_lock import FileMutex


class UploadSessions:
    def __init__(self, root: Path, config: IndexingConfig) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "uploads.sqlite3"
        self.mutex = FileMutex(root / ".uploads.lock")
        self.config = config
        with closing(self.connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS uploads (id TEXT PRIMARY KEY, tenant TEXT, "
                "meta TEXT, result TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS parts (upload TEXT, number INTEGER, "
                "body BLOB, PRIMARY KEY(upload,number))"
            )

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=60)

    def init(
        self, tenant: str, *, name: str, size: int, source_uri: str = "", content_sha256: str = ""
    ) -> dict[str, Any]:
        if not 0 < size <= self.config.max_upload_bytes:
            raise ValueError("Upload exceeds configured size cap")
        uid = str(uuid4())
        part_size = self.config.upload_part_bytes
        meta = {
            "id": uid,
            "name": name,
            "size": size,
            "part_bytes": part_size,
            "parts": (size + part_size - 1) // part_size,
            "source_uri": source_uri or f"upload://{name}",
            "content_sha256": content_sha256,
            "expires_at": time.time() + self.config.upload_expiry_seconds,
        }
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO uploads VALUES (?,?,?,NULL)", (uid, tenant, json.dumps(meta)))
        return meta

    def get(self, uid: str, tenant: str) -> dict[str, Any]:
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT meta,result FROM uploads WHERE id=? AND tenant=?", (uid, tenant)
            ).fetchone()
            if row is None:
                raise KeyError(uid)
            meta: dict[str, Any] = json.loads(row[0])
            if meta["expires_at"] < time.time():
                raise KeyError(uid)
            meta["received"] = [
                r[0]
                for r in db.execute(
                    "SELECT number FROM parts WHERE upload=? ORDER BY number", (uid,)
                )
            ]
            meta["result"] = json.loads(row[1]) if row[1] else None
            return meta

    def put(self, uid: str, tenant: str, *, number: int, body: bytes) -> None:
        with self.mutex.hold():
            meta = self.get(uid, tenant)
            if meta["result"]:
                raise ValueError("Upload already completed")
            expected = min(meta["part_bytes"], meta["size"] - number * meta["part_bytes"])
            if not 0 <= number < meta["parts"] or len(body) != expected:
                raise ValueError("Part number or size does not match upload manifest")
            with closing(self.connect()) as db, db:
                old = db.execute(
                    "SELECT body FROM parts WHERE upload=? AND number=?", (uid, number)
                ).fetchone()
                if old and old[0] != body:
                    raise ValueError("Part already exists with different content")
                db.execute("INSERT OR REPLACE INTO parts VALUES (?,?,?)", (uid, number, body))

    def content(self, uid: str, tenant: str) -> bytes:
        meta = self.get(uid, tenant)
        if meta["received"] != list(range(meta["parts"])):
            raise ValueError("Upload has missing parts")
        with closing(self.connect()) as db:
            body = b"".join(
                r[0]
                for r in db.execute("SELECT body FROM parts WHERE upload=? ORDER BY number", (uid,))
            )
        if len(body) != meta["size"]:
            raise ValueError("Upload size mismatch")
        if meta["content_sha256"] and hashlib.sha256(body).hexdigest() != meta["content_sha256"]:
            raise ValueError("Upload content hash mismatch")
        return body

    def finish(self, uid: str, tenant: str, result: dict[str, Any]) -> None:
        self.get(uid, tenant)
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE uploads SET result=? WHERE id=? AND tenant=?",
                (json.dumps(result), uid, tenant),
            )
            db.execute("DELETE FROM parts WHERE upload=?", (uid,))

    def prune(self, *, tenant_id: str) -> int:
        """Remove expired sessions and their parts (maintenance command)."""
        with self.mutex.hold(), closing(self.connect()) as db, db:
            expired = [
                r[0]
                for r in db.execute("SELECT id,meta FROM uploads WHERE tenant=?", (tenant_id,))
                if json.loads(r[1])["expires_at"] < time.time()
            ]
            db.executemany("DELETE FROM parts WHERE upload=?", [(uid,) for uid in expired])
            db.executemany("DELETE FROM uploads WHERE id=?", [(uid,) for uid in expired])
        return len(expired)
