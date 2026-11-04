"""Durable tenant-scoped model drafts, test receipts and activation pointers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from agentic_graphrag.core.models import ModelProfile, ModelProfileTest


class ProfileConflict(ValueError):
    """The caller edited or activated an obsolete revision."""


class ModelProfileStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connection() as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS profiles (tenant TEXT NOT NULL, id TEXT NOT NULL, "
                "revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(tenant,id,revision));"
                "CREATE TABLE IF NOT EXISTS profile_tests (tenant TEXT NOT NULL, id TEXT NOT NULL, "
                "revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(tenant,id,revision));"
                "CREATE TABLE IF NOT EXISTS active_profiles (tenant TEXT NOT NULL, "
                "id TEXT NOT NULL, "
                "revision INTEGER NOT NULL, PRIMARY KEY(tenant,id));"
                "CREATE TABLE IF NOT EXISTS profile_activations (tenant TEXT NOT NULL, "
                "id TEXT NOT NULL, revision INTEGER NOT NULL, actor TEXT NOT NULL, created TEXT);"
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_draft(self, profile: ModelProfile, *, expected_revision: int) -> ModelProfile:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT MAX(revision) FROM profiles WHERE tenant=? AND id=?",
                (profile.tenant_id, profile.id),
            ).fetchone()
            latest = int(row[0] or 0)
            if latest != expected_revision:
                raise ProfileConflict("Model profile changed; reload its latest revision")
            saved = profile.model_copy(
                update={"revision": latest + 1, "created_at": datetime.now(UTC).isoformat()}
            )
            db.execute(
                "INSERT INTO profiles VALUES (?,?,?,?)",
                (saved.tenant_id, saved.id, saved.revision, saved.model_dump_json()),
            )
            return saved

    def get(self, profile_id: str, *, tenant_id: str, revision: int | None = None) -> ModelProfile:
        with self._connection() as db:
            row = db.execute(
                "SELECT body FROM profiles WHERE tenant=? AND id=? "
                "AND (? IS NULL OR revision=?) ORDER BY revision DESC LIMIT 1",
                (tenant_id, profile_id, revision, revision),
            ).fetchone()
        if row is None:
            raise KeyError("Model profile not found")
        return ModelProfile.model_validate_json(row[0])

    def list(self, *, tenant_id: str) -> list[ModelProfile]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT body FROM profiles p WHERE tenant=? AND revision=(SELECT MAX(revision) "
                "FROM profiles WHERE tenant=p.tenant AND id=p.id) ORDER BY id",
                (tenant_id,),
            ).fetchall()
        return [ModelProfile.model_validate_json(row[0]) for row in rows]

    def save_test(self, report: ModelProfileTest, *, tenant_id: str) -> None:
        self.get(report.profile_id, tenant_id=tenant_id, revision=report.revision)
        with self._connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO profile_tests VALUES (?,?,?,?)",
                (tenant_id, report.profile_id, report.revision, report.model_dump_json()),
            )

    def test_result(
        self, profile_id: str, *, tenant_id: str, revision: int
    ) -> ModelProfileTest | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT body FROM profile_tests WHERE tenant=? AND id=? AND revision=?",
                (tenant_id, profile_id, revision),
            ).fetchone()
        return ModelProfileTest.model_validate_json(row[0]) if row else None

    def active_revision(self, profile_id: str, *, tenant_id: str) -> int:
        with self._connection() as db:
            row = db.execute(
                "SELECT revision FROM active_profiles WHERE tenant=? AND id=?",
                (tenant_id, profile_id),
            ).fetchone()
        return int(row[0]) if row else 0

    def active(self, profile_id: str, *, tenant_id: str) -> ModelProfile:
        revision = self.active_revision(profile_id, tenant_id=tenant_id)
        if not revision:
            raise KeyError("Model profile has no published revision")
        return self.get(profile_id, tenant_id=tenant_id, revision=revision)

    def published(self, profile_id: str, *, tenant_id: str, revision: int) -> ModelProfile:
        with self._connection() as db:
            row = db.execute(
                "SELECT 1 FROM profile_activations WHERE tenant=? AND id=? AND revision=? LIMIT 1",
                (tenant_id, profile_id, revision),
            ).fetchone()
        if row is None:
            raise KeyError("Model profile revision has not been published")
        return self.get(profile_id, tenant_id=tenant_id, revision=revision)

    def publish(self, profile: ModelProfile, *, expected_active_revision: int, actor: str) -> None:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            args = (profile.tenant_id, profile.id)
            row = db.execute(
                "SELECT revision FROM active_profiles WHERE tenant=? AND id=?", args
            ).fetchone()
            if int(row[0] if row else 0) != expected_active_revision:
                raise ProfileConflict("Active model revision changed; reload before publishing")
            report = self.test_result(profile.id, tenant_id=args[0], revision=profile.revision)
            if not report or not report.passed:
                raise ProfileConflict("The selected revision must pass capability tests")
            if report.catalog_fingerprint != profile.catalog_fingerprint:
                raise ProfileConflict("Capability test does not match the model catalogue")
            db.execute(
                "INSERT OR REPLACE INTO active_profiles VALUES (?,?,?)", (*args, profile.revision)
            )
            db.execute(
                "INSERT INTO profile_activations VALUES (?,?,?,?,?)",
                (*args, profile.revision, actor, datetime.now(UTC).isoformat()),
            )
