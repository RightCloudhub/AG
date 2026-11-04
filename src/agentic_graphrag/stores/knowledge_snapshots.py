"""Immutable generation manifests, payloads and irrevocable source tombstones."""

import hashlib
import json

from agentic_graphrag.core.knowledge import KnowledgeBase, KnowledgeScope, SummaryRecord
from agentic_graphrag.core.knowledge_versions import KnowledgeSnapshot
from agentic_graphrag.stores.knowledge_database import read_kb, scope_params, write_kb
from agentic_graphrag.stores.knowledge_journal import KnowledgeJournal


class KnowledgeSnapshots(KnowledgeJournal):
    def save_snapshot(self, snapshot: KnowledgeSnapshot, payload: str) -> KnowledgeSnapshot:
        canonical = json.dumps(json.loads(payload), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        if snapshot.payload_sha256 and snapshot.payload_sha256 != digest:
            raise ValueError("Snapshot payload does not match its manifest hash")
        result = snapshot.model_copy(update={"payload_sha256": digest})
        key = (*scope_params(snapshot.scope), snapshot.generation)
        with self.transaction() as db:
            row = db.execute(
                "SELECT manifest,payload FROM knowledge_snapshots "
                "WHERE tenant=? AND kb=? AND generation=?",
                key,
            ).fetchone()
            if row:
                stored = KnowledgeSnapshot.model_validate_json(row[0])
                if stored.model_dump(exclude={"created_at"}) != result.model_dump(
                    exclude={"created_at"}
                ):
                    raise ValueError("A sealed generation cannot be overwritten")
                return stored
            db.execute(
                "INSERT INTO knowledge_snapshots VALUES (?,?,?,?,?)",
                (*key, result.model_dump_json(), canonical),
            )
        return result

    def get_snapshot(
        self, scope: KnowledgeScope, generation: str
    ) -> tuple[KnowledgeSnapshot, str] | None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT manifest,payload FROM knowledge_snapshots "
                "WHERE tenant=? AND kb=? AND generation=?",
                (*scope_params(scope), generation),
            ).fetchone()
        if not row:
            return None
        manifest = KnowledgeSnapshot.model_validate_json(row[0])
        if hashlib.sha256(row[1].encode()).hexdigest() != manifest.payload_sha256:
            raise ValueError("Snapshot payload integrity check failed")
        return manifest, row[1]

    def publish(
        self, scope: KnowledgeScope, *, generation: str, expected_revision: int
    ) -> KnowledgeBase:
        with self.transaction() as db:
            kb = read_kb(db, scope)
            exists = db.execute(
                "SELECT 1 FROM knowledge_snapshots WHERE tenant=? AND kb=? AND generation=?",
                (*scope_params(scope), generation),
            ).fetchone()
            if kb is None or not exists:
                raise ValueError("Only a sealed knowledge base generation can be published")
            updated = KnowledgeBase.model_validate(
                {
                    **kb.model_dump(),
                    "active_generation": generation,
                    "config_revision": expected_revision + 1,
                }
            )
            write_kb(db, updated, expected_revision=expected_revision)
        return updated

    def tombstone(self, scope: KnowledgeScope, doc_id: str, *, through_version: int) -> None:
        if through_version < 1 or not doc_id:
            raise ValueError("A tombstone needs a document and a positive version")
        with self.transaction() as db:
            db.execute(
                "INSERT INTO source_tombstones VALUES (?,?,?,?) "
                "ON CONFLICT(tenant,kb,doc_id) DO UPDATE SET through_version="
                "max(through_version,excluded.through_version)",
                (*scope_params(scope), doc_id, through_version),
            )

    def is_tombstoned(self, scope: KnowledgeScope, doc_id: str, *, version: int) -> bool:
        with self.transaction() as db:
            row = db.execute(
                "SELECT through_version FROM source_tombstones "
                "WHERE tenant=? AND kb=? AND doc_id=?",
                (*scope_params(scope), doc_id),
            ).fetchone()
        return bool(row and version <= int(row[0]))

    def save_summary(self, summary: SummaryRecord) -> None:
        key = (*scope_params(summary.scope), summary.generation, summary.summary_id)
        with self.transaction() as db:
            existing = db.execute(
                "SELECT fingerprint FROM knowledge_summaries "
                "WHERE tenant=? AND kb=? AND generation=? AND id=?",
                key,
            ).fetchone()
            if existing and existing[0] != summary.input_fingerprint:
                raise ValueError("A summary generation cannot change its input fingerprint")
            db.execute(
                "INSERT OR REPLACE INTO knowledge_summaries VALUES (?,?,?,?,?,?)",
                (*key, summary.input_fingerprint, summary.model_dump_json()),
            )

    def summaries(self, scope: KnowledgeScope, *, generation: str) -> list[SummaryRecord]:
        with self.transaction() as db:
            rows = db.execute(
                "SELECT payload FROM knowledge_summaries WHERE tenant=? AND kb=? AND generation=? "
                "ORDER BY id",
                (*scope_params(scope), generation),
            ).fetchall()
        return [SummaryRecord.model_validate_json(row[0]) for row in rows]
